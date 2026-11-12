import os
import shutil
import sqlite3
import subprocess
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from PIL import Image

from .config import limits
from .media import ffmpeg, overlay
from .models import Project, dimensions, now, uid
from .store import Conflict, Store


def stamp(seconds: float) -> str:
    ms = round(seconds * 1000)
    return f"{ms // 3600000:02}:{ms // 60000 % 60:02}:{ms // 1000 % 60:02},{ms % 1000:03}"


def subtitles(project: Project) -> str:
    result, start = [], 0.0
    for scene in project.scenes:
        end = start + round(scene.duration * project.fps) / project.fps
        if scene.caption_words:
            for word in scene.caption_words:
                word_start, word_end = start + word.start, min(end, start + word.end)
                if word_end > word_start:
                    result.append(
                        f"{len(result) + 1}\n{stamp(word_start)} --> {stamp(word_end)}\n{word.text}\n"
                    )
        elif scene.subtitle:
            result.append(f"{len(result) + 1}\n{stamp(start)} --> {stamp(end)}\n{scene.subtitle}\n")
        start = end
    return "\n".join(result)


class RenderPool(ThreadPoolExecutor):
    """Executor with a stoppable dispatcher; idle polling never blocks interpreter exit."""

    def __init__(self, renderer, autostart: bool):
        super().__init__(max_workers=1, thread_name_prefix="openreel-render")
        self.stopped = threading.Event()
        self.dispatcher = None
        if autostart:
            self.dispatcher = threading.Thread(target=renderer.run_worker, daemon=True, name="openreel-queue")
            self.dispatcher.start()

    def shutdown(self, wait=True, *, cancel_futures=False):
        self.stopped.set()
        if self.dispatcher and self.dispatcher is not threading.current_thread():
            self.dispatcher.join(timeout=2)
        super().shutdown(wait=wait, cancel_futures=cancel_futures)


def caption_track(scene, size: tuple[int, int], work: Path, index: int, fps: int) -> list[str]:
    """Render timestamped caption images with bounded memory and a small FFmpeg graph."""
    if not scene.caption_words:
        art = work / f"overlay-{index}.png"
        overlay(scene, size, art)
        return ["-loop", "1", "-framerate", str(fps), "-i", str(art)]
    frames = round(scene.duration * fps)
    events = {0: "", frames: ""}
    for word in scene.caption_words:
        first, last = round(word.start * fps), min(frames, round(word.end * fps))
        if last > first:
            events[first] = word.text
            events[last] = ""
    steps = sorted(events)
    entries = []
    for j, (first, last) in enumerate(zip(steps, steps[1:])):
        art = work / f"caption-{index}-{j}.png"
        overlay(scene.model_copy(update={"subtitle": events[first], "caption_words": []}), size, art)
        entries.extend(
            [f"file '{art.name}'", f"option framerate {fps}", f"duration {(last - first) / fps:.9f}"]
        )
    # The concat demuxer needs the final image repeated to honor its duration.
    entries.extend([f"file '{art.name}'", f"option framerate {fps}"])
    track = work / f"captions-{index}.txt"
    track.write_text("\n".join(entries), encoding="utf-8")
    return ["-f", "concat", "-safe", "0", "-i", str(track)]


class Renderer:
    def __init__(self, store: Store, autostart: bool | None = None):
        self.store = store
        self.worker_id = f"local-{os.getpid()}-{uid()[:12]}"
        if autostart is None:
            autostart = os.environ.get("OPENREEL_RENDER_WORKER", "1") != "0"
        # Dispatcher can access pool only after assignment, so start it afterward.
        self.pool = RenderPool(self, False)
        if autostart:
            self.pool.dispatcher = threading.Thread(
                target=self.run_worker, daemon=True, name="openreel-queue"
            )
            self.pool.dispatcher.start()

    def run_worker(self):
        active = None
        while not self.pool.stopped.wait(0.2):
            if active is not None and not active.done():
                continue
            try:
                job = self.store.claim_job(self.worker_id)
            except sqlite3.OperationalError:
                # A busy writer or a temporarily unavailable disk must not kill polling.
                self.pool.stopped.wait(2)
                continue
            if job is None:
                continue
            try:
                project = Project.model_validate(job["project_snapshot"])
                active = self.pool.submit(self._render, project, job)
            except Exception as exc:
                self.store.update_leased_job(job["id"], job["lease_token"], status="failed", error=str(exc))

    def submit(self, project_id: str, resolution: str) -> dict:
        if resolution not in {"draft", "720p", "1080p"}:
            raise ValueError("Resolution must be draft, 720p, or 1080p.")
        project = self.store.get(project_id)
        if not project.scenes:
            raise ValueError("Add at least one scene before exporting.")
        self.store.validate_assets(project)
        job = dict(
            id=uid(),
            project_id=project_id,
            revision=project.revision,
            status="queued",
            progress=0,
            resolution=resolution,
            project_snapshot=project.model_dump(),
            created_at=now(),
            error=None,
            cancel=False,
        )
        self.store.new_job(job)
        return self.store.public_job(job)

    def cancel(self, job_id: str) -> dict:
        return self.store.cancel_job(job_id)

    def _run(self, args: list[str], job_id: str, cwd: Path, lease_lost: threading.Event):
        with tempfile.TemporaryFile() as log:
            proc = subprocess.Popen(
                [
                    ffmpeg(),
                    "-nostdin",
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-y",
                    "-filter_complex_threads",
                    "1",
                    *args,
                ],
                cwd=cwd,
                stdout=subprocess.DEVNULL,
                stderr=log,
            )
            deadline = time.monotonic() + limits().render_stage_timeout_seconds
            try:
                while proc.poll() is None:
                    if lease_lost.is_set():
                        raise Conflict("Render lease expired or belongs to another worker.")
                    if self.store.job(job_id)["cancel"]:
                        raise InterruptedError("Export canceled")
                    if time.monotonic() > deadline:
                        raise TimeoutError("A render stage exceeded its configured time limit.")
                    time.sleep(0.15)
                if proc.returncode:
                    log.seek(0)
                    message = log.read()[-2000:].decode(errors="replace")
                    raise RuntimeError("FFmpeg failed: " + message)
            finally:
                if proc.poll() is None:
                    proc.kill()
                proc.wait()

    def _render(self, project: Project, job: dict):
        job_id = job["id"]
        token = job["lease_token"]
        output_dir = self.store.root / "renders" / job_id / uid()
        heartbeat_stop = threading.Event()
        lease_lost = threading.Event()

        def heartbeat():
            while not heartbeat_stop.wait(10):
                try:
                    if not self.store.heartbeat_job(job_id, token):
                        lease_lost.set()
                        return
                except Exception:
                    lease_lost.set()
                    return

        heart = threading.Thread(target=heartbeat, daemon=True, name=f"openreel-heartbeat-{job_id[:12]}")

        def update(**changes):
            return self.store.update_leased_job(job_id, token, **changes)

        work = None
        try:
            work = Path(tempfile.mkdtemp(prefix="render-", dir=self.store.root / "scratch"))
            heart.start()
            if self.store.job(job_id)["cancel"]:
                raise InterruptedError("Export canceled")
            update(status="rendering", progress=2)
            size = dimensions(project.aspect_ratio, job["resolution"])
            w, h = size
            fps = project.fps
            for i, scene in enumerate(project.scenes):
                frames = round(scene.duration * fps)
                duration = frames / fps
                overlays = caption_track(scene, size, work, i, fps)
                asset = self.store.asset(scene.asset_id) if scene.asset_id else None
                if asset:
                    source = self.store.asset_path(asset.id)
                else:
                    source = work / f"background-{i}.png"
                    Image.new("RGB", size, scene.color).save(source)
                if asset and asset.kind == "video":
                    inputs = ["-stream_loop", "-1", "-protocol_whitelist", "file,pipe", "-i", str(source)]
                    motion = (
                        f"fps={fps},scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},setsar=1"
                    )
                else:
                    inputs = ["-loop", "1", "-framerate", str(fps), "-i", str(source)]
                    z = {
                        "still": "1",
                        "push_in": f"1+0.12*on/{frames}",
                        "pull_out": f"1.12-0.12*on/{frames}",
                        "pan_left": "1.12",
                        "pan_right": "1.12",
                    }[scene.motion]
                    x = {
                        "pan_left": f"(iw-iw/zoom)*(1-on/{frames})",
                        "pan_right": f"(iw-iw/zoom)*on/{frames}",
                    }.get(scene.motion, "iw/2-iw/zoom/2")
                    motion = (
                        f"scale={w * 2}:{h * 2}:force_original_aspect_ratio=increase,crop={w * 2}:{h * 2},"
                        f"zoompan=z='{z}':x='{x}':y='ih/2-ih/zoom/2':d=1:s={w}x{h}:fps={fps},setsar=1"
                    )
                inputs += overlays
                if scene.audio_asset_id:
                    inputs += [
                        "-protocol_whitelist",
                        "file,pipe",
                        "-i",
                        str(self.store.asset_path(scene.audio_asset_id)),
                    ]
                else:
                    inputs += ["-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo"]
                fade = min(0.3, duration / 4)
                transition = (
                    f",fade=t=in:st=0:d={fade},fade=t=out:st={duration - fade}:d={fade}"
                    if scene.transition == "fade"
                    else ""
                )
                graph = (
                    f"[0:v]{motion},setpts=PTS-STARTPTS[bg];"
                    f"[bg][1:v]overlay=0:0:shortest=1{transition},format=yuv420p[v];"
                    f"[2:a]aresample=48000,apad,atrim=duration={duration},asetpts=PTS-STARTPTS[a]"
                )
                self._run(
                    inputs
                    + [
                        "-filter_complex",
                        graph,
                        "-map",
                        "[v]",
                        "-map",
                        "[a]",
                        "-t",
                        str(duration),
                        "-r",
                        str(fps),
                        "-c:v",
                        "libx264",
                        "-preset",
                        "veryfast",
                        "-crf",
                        "20",
                        "-threads",
                        "2",
                        "-c:a",
                        "pcm_s16le",
                        "-ar",
                        "48000",
                        "-ac",
                        "2",
                        str(work / f"scene-{i}.mov"),
                    ],
                    job_id,
                    work,
                    lease_lost,
                )
                update(progress=5 + round(80 * (i + 1) / len(project.scenes)))
            (work / "concat.txt").write_text(
                "\n".join(
                    f"file 'scene-{i}.mov'\nduration {round(scene.duration * fps) / fps:.9f}"
                    for i, scene in enumerate(project.scenes)
                )
            )
            # Keep scene audio as PCM until the final export. Independently
            # encoded AAC scenes introduce encoder priming at every cut.
            self._run(
                ["-f", "concat", "-safe", "1", "-i", "concat.txt", "-c", "copy", "joined.mov"],
                job_id,
                work,
                lease_lost,
            )
            output_dir.mkdir(parents=True, exist_ok=True)
            video = output_dir / "video.mp4"
            if project.music_asset_id:
                self._run(
                    [
                        "-i",
                        "joined.mov",
                        "-stream_loop",
                        "-1",
                        "-protocol_whitelist",
                        "file,pipe",
                        "-i",
                        str(self.store.asset_path(project.music_asset_id)),
                        "-filter_complex",
                        f"[1:a]volume={project.music_volume}[music];[0:a][music]amix=inputs=2:duration=first:normalize=0,alimiter=limit=0.95[a]",
                        "-map",
                        "0:v",
                        "-map",
                        "[a]",
                        "-c:v",
                        "copy",
                        "-c:a",
                        "aac",
                        "-movflags",
                        "+faststart",
                        str(video),
                    ],
                    job_id,
                    work,
                    lease_lost,
                )
            else:
                self._run(
                    [
                        "-i",
                        "joined.mov",
                        "-c:v",
                        "copy",
                        "-c:a",
                        "aac",
                        "-movflags",
                        "+faststart",
                        str(video),
                    ],
                    job_id,
                    work,
                    lease_lost,
                )
            (output_dir / "subtitles.srt").write_text(subtitles(project), encoding="utf-8")
            (output_dir / "project.json").write_text(project.model_dump_json(indent=2), encoding="utf-8")
            if self.store.job(job_id)["cancel"]:
                raise InterruptedError("Export canceled")
            update(
                status="completed",
                progress=100,
                completed_at=now(),
                video_url=f"/api/renders/{job_id}/video.mp4",
                subtitles_url=f"/api/renders/{job_id}/subtitles.srt",
                video_path=str(video),
            )
        except InterruptedError:
            shutil.rmtree(output_dir, ignore_errors=True)
            try:
                update(status="canceled", error=None)
            except Conflict:
                pass
        except Exception as exc:
            shutil.rmtree(output_dir, ignore_errors=True)
            try:
                update(status="failed", error=str(exc)[-2200:])
            except Conflict:
                pass
        finally:
            heartbeat_stop.set()
            if heart.is_alive():
                heart.join(timeout=2)
            if work is not None:
                shutil.rmtree(work, ignore_errors=True)
