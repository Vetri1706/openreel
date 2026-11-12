import os
import shutil
import subprocess
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from PIL import Image

from .media import ffmpeg, overlay
from .models import Project, dimensions, now, uid
from .store import Store


def stamp(seconds: float) -> str:
    ms = round(seconds * 1000)
    return f"{ms // 3600000:02}:{ms // 60000 % 60:02}:{ms // 1000 % 60:02},{ms % 1000:03}"


def subtitles(project: Project) -> str:
    result, start = [], 0.0
    for scene in project.scenes:
        end = start + round(scene.duration * project.fps) / project.fps
        if scene.subtitle:
            result.append(f"{len(result) + 1}\n{stamp(start)} --> {stamp(end)}\n{scene.subtitle}\n")
        start = end
    return "\n".join(result)


class Renderer:
    def __init__(self, store: Store):
        self.store = store
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="openreel-render")

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
            pid=os.getpid(),
            created_at=now(),
            error=None,
            cancel=False,
        )
        self.store.new_job(job)
        self.pool.submit(self._render, project, job)
        return job

    def cancel(self, job_id: str) -> dict:
        job = self.store.job(job_id)
        if job["status"] not in {"queued", "rendering"}:
            return job
        return self.store.update_job(job_id, cancel=True)

    def _run(self, args: list[str], job_id: str, cwd: Path):
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
            deadline = time.monotonic() + 1200
            try:
                while proc.poll() is None:
                    if self.store.job(job_id)["cancel"]:
                        raise InterruptedError("Export canceled")
                    if time.monotonic() > deadline:
                        raise TimeoutError("A render stage exceeded 20 minutes.")
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
        output_dir = self.store.root / "renders" / job_id
        work = Path(tempfile.mkdtemp(prefix="render-", dir=self.store.root / "scratch"))
        try:
            if self.store.job(job_id)["cancel"]:
                raise InterruptedError("Export canceled")
            self.store.update_job(job_id, status="rendering", progress=2)
            size = dimensions(project.aspect_ratio, job["resolution"])
            w, h = size
            fps = project.fps
            for i, scene in enumerate(project.scenes):
                frames = round(scene.duration * fps)
                duration = frames / fps
                art = work / f"overlay-{i}.png"
                overlay(scene, size, art)
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
                inputs += ["-loop", "1", "-framerate", str(fps), "-i", str(art)]
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
                        "aac",
                        "-ar",
                        "48000",
                        "-ac",
                        "2",
                        str(work / f"scene-{i}.mp4"),
                    ],
                    job_id,
                    work,
                )
                self.store.update_job(job_id, progress=5 + round(80 * (i + 1) / len(project.scenes)))
            (work / "concat.txt").write_text(
                "\n".join(f"file 'scene-{i}.mp4'" for i in range(len(project.scenes)))
            )
            self._run(
                ["-f", "concat", "-safe", "1", "-i", "concat.txt", "-c", "copy", "joined.mp4"], job_id, work
            )
            output_dir.mkdir(exist_ok=True)
            video = output_dir / "video.mp4"
            if project.music_asset_id:
                self._run(
                    [
                        "-i",
                        "joined.mp4",
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
                )
            else:
                self._run(
                    ["-i", "joined.mp4", "-c", "copy", "-movflags", "+faststart", str(video)], job_id, work
                )
            (output_dir / "subtitles.srt").write_text(subtitles(project), encoding="utf-8")
            (output_dir / "project.json").write_text(project.model_dump_json(indent=2), encoding="utf-8")
            if self.store.job(job_id)["cancel"]:
                raise InterruptedError("Export canceled")
            self.store.update_job(
                job_id,
                status="completed",
                progress=100,
                completed_at=now(),
                video_url=f"/api/renders/{job_id}/video.mp4",
                subtitles_url=f"/api/renders/{job_id}/subtitles.srt",
                video_path=str(video),
            )
        except InterruptedError:
            shutil.rmtree(output_dir, ignore_errors=True)
            self.store.update_job(job_id, status="canceled", error=None)
        except Exception as exc:
            shutil.rmtree(output_dir, ignore_errors=True)
            self.store.update_job(job_id, status="failed", error=str(exc)[-2200:])
        finally:
            shutil.rmtree(work, ignore_errors=True)
