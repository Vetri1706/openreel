"""Owner-configured HTTP render workers; SQLite stays on the coordinator."""

import hmac
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from fastapi import Depends, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import Field

from .config import limits
from .media import ffmpeg
from .models import Asset, Model, Project, now, uid
from .render import Renderer, subtitles
from .store import Conflict, Store


def asset_ids(project: Project) -> set[str]:
    refs = {project.music_asset_id}
    refs.update(s.asset_id for s in project.scenes)
    refs.update(s.audio_asset_id for s in project.scenes)
    return refs - {None}


class ClaimRequest(Model):
    worker_id: str = Field(min_length=1, max_length=128)


class FailureRequest(Model):
    error: str = Field(min_length=1, max_length=2000)


def install_worker_routes(app, service):
    def authenticate(request: Request):
        key = os.environ.get("OPENREEL_WORKER_KEY", "")
        supplied = request.headers.get("authorization", "").removeprefix("Bearer ")
        if len(key) < 32 or not hmac.compare_digest(supplied, key):
            raise HTTPException(401, "A configured render worker key is required.")

    def lease(request: Request, job_id: str):
        return service.store.leased_job(job_id, request.headers.get("x-openreel-lease", ""))

    @app.post("/api/workers/claim", dependencies=[Depends(authenticate)])
    def claim(body: ClaimRequest):
        job = service.store.claim_job(body.worker_id)
        if not job:
            return {"job": None}
        project = Project.model_validate(job["project_snapshot"])
        return {"job": job, "assets": [service.store.asset(a).model_dump() for a in asset_ids(project)]}

    @app.post("/api/workers/jobs/{job_id}/heartbeat", dependencies=[Depends(authenticate)])
    def heartbeat(job_id: str, request: Request):
        token = request.headers.get("x-openreel-lease", "")
        if not service.store.heartbeat_job(job_id, token):
            raise Conflict("Render lease expired or was reassigned.")
        job = lease(request, job_id)
        return {"cancel": job["cancel"], "lease_expires": job["lease_expires"]}

    @app.get("/api/workers/jobs/{job_id}/assets/{asset_id}", dependencies=[Depends(authenticate)])
    def download_asset(job_id: str, asset_id: str, request: Request):
        job = lease(request, job_id)
        if asset_id not in asset_ids(Project.model_validate(job["project_snapshot"])):
            raise KeyError("Asset is not part of this render snapshot")
        return FileResponse(service.store.asset_path(asset_id))

    @app.post("/api/workers/jobs/{job_id}/failed", dependencies=[Depends(authenticate)])
    def failed(job_id: str, body: FailureRequest, request: Request):
        job = lease(request, job_id)
        return service.store.update_leased_job(
            job_id,
            job["lease_token"],
            status="canceled" if job["cancel"] else "failed",
            error=body.error,
        )

    @app.post("/api/workers/jobs/{job_id}/complete", dependencies=[Depends(authenticate)])
    async def complete(job_id: str, request: Request):
        from starlette.concurrency import run_in_threadpool

        job = lease(request, job_id)
        if job["cancel"]:
            raise Conflict("Render was canceled.")
        # Render output can exceed a single source asset's size. Still enforce a finite bound.
        maximum = limits().max_upload_bytes * 20
        if int(request.headers.get("content-length", 0)) > maximum:
            raise HTTPException(413, "Rendered video exceeds the worker output limit.")
        folder = service.store.root / "renders" / job_id / uid()
        folder.mkdir(parents=True)
        video = folder / "video.mp4"
        published = False
        try:
            count, pulse = 0, time.monotonic()
            with video.open("wb") as output:
                async for chunk in request.stream():
                    count += len(chunk)
                    if count > maximum:
                        raise HTTPException(413, "Rendered video exceeds the worker output limit.")
                    output.write(chunk)
                    if time.monotonic() - pulse > 15:
                        if not service.store.heartbeat_job(job_id, job["lease_token"]):
                            raise Conflict("Render lease expired.")
                        pulse = time.monotonic()
            if not count:
                raise ValueError("Worker returned an empty video.")
            await run_in_threadpool(validate_output, video)
            latest = lease(request, job_id)
            if latest["cancel"]:
                raise Conflict("Render was canceled.")
            project = Project.model_validate(job["project_snapshot"])
            (folder / "project.json").write_text(project.model_dump_json(indent=2), encoding="utf-8")
            (folder / "subtitles.srt").write_text(subtitles(project), encoding="utf-8")
            result = service.store.update_leased_job(
                job_id,
                job["lease_token"],
                status="completed",
                progress=100,
                completed_at=now(),
                video_path=str(video),
                video_url=f"/api/renders/{job_id}/video.mp4",
                subtitles_url=f"/api/renders/{job_id}/subtitles.srt",
                error=None,
            )
            published = True
            return result
        finally:
            if not published:
                shutil.rmtree(folder, ignore_errors=True)


def validate_output(video: Path):
    try:
        result = subprocess.run(
            [
                ffmpeg(),
                "-nostdin",
                "-v",
                "error",
                "-protocol_whitelist",
                "file,pipe",
                "-i",
                str(video),
                "-map",
                "0:v:0",
                "-map",
                "0:a:0",
                "-t",
                "0.1",
                "-f",
                "null",
                "-",
            ],
            capture_output=True,
            timeout=25,
        )
    except subprocess.TimeoutExpired as exc:
        raise ValueError("Worker output could not be validated within 25 seconds.") from exc
    if result.returncode:
        raise ValueError("Worker output must be a decodable video with audio.")


def _chunks(path: Path):
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            yield chunk


def run_once(client: httpx.Client, root: Path, worker_id: str) -> bool:
    response = client.post("/api/workers/claim", json={"worker_id": worker_id})
    response.raise_for_status()
    claim = response.json()
    job = claim["job"]
    if not job:
        return False
    prefix = f"/api/workers/jobs/{job['id']}"
    headers = {"X-OpenReel-Lease": job["lease_token"]}
    stop, lost = threading.Event(), threading.Event()

    def pulse():
        while not stop.wait(10):
            try:
                reply = client.post(prefix + "/heartbeat", headers=headers)
                reply.raise_for_status()
                if reply.json()["cancel"]:
                    lost.set()
                    return
            except (httpx.HTTPError, ValueError, KeyError):
                lost.set()
                return

    heartbeat = threading.Thread(target=pulse, daemon=True)
    renderer = None
    local_job = None
    temporary = None
    try:
        root.mkdir(parents=True, exist_ok=True)
        temporary = tempfile.mkdtemp(prefix="remote-render-", dir=root)
        heartbeat.start()
        store = Store(Path(temporary))
        project = Project.model_validate(job["project_snapshot"])
        store.create(project)
        for value in claim["assets"]:
            asset = Asset.model_validate(value)
            if asset.id not in asset_ids(project) or Path(asset.filename).name != asset.filename:
                raise ValueError("Coordinator supplied invalid asset metadata.")
            path = store.root / "assets" / asset.filename
            count = 0
            with client.stream("GET", prefix + "/assets/" + asset.id, headers=headers) as result:
                result.raise_for_status()
                with path.open("wb") as output:
                    for chunk in result.iter_bytes(1024 * 1024):
                        if lost.is_set():
                            raise InterruptedError("Remote lease lost or canceled.")
                        count += len(chunk)
                        if count > asset.size or count > limits().max_upload_bytes:
                            raise ValueError("Asset exceeds its declared or configured size.")
                        output.write(chunk)
            if count != asset.size:
                raise ValueError("Asset download was incomplete.")
            store.add_asset(asset)
        # Character references are notes, not renderer inputs; omit them from this isolated snapshot.
        for character in project.characters:
            character.reference_asset_id = None
        project = store.save(project)
        renderer = Renderer(store, autostart=True)
        local_job = renderer.submit(project.id, job["resolution"])
        while True:
            if lost.is_set():
                renderer.cancel(local_job["id"])
                raise InterruptedError("Remote lease lost or canceled.")
            result = store.job(local_job["id"])
            if result["status"] == "completed":
                break
            if result["status"] in {"failed", "canceled"}:
                raise ValueError(result.get("error") or "Local export was canceled.")
            stop.wait(0.2)
        video = Path(result["video_path"])
        upload = client.post(
            prefix + "/complete",
            headers={**headers, "Content-Length": str(video.stat().st_size)},
            content=_chunks(video),
            timeout=120,
        )
        upload.raise_for_status()
        renderer.pool.shutdown(wait=True)
        renderer = None
    except BaseException as exc:
        print(
            f"Render worker job {job['id']} failed ({type(exc).__name__}). See its server job status.",
            file=sys.stderr,
            flush=True,
        )
        if renderer:
            if local_job:
                renderer.cancel(local_job["id"])
            renderer.pool.shutdown(wait=True)
        try:
            client.post(
                prefix + "/failed", headers=headers, json={"error": str(exc)[:2000] or "Worker stopped"}
            )
        except httpx.HTTPError:
            pass  # A disconnected worker's lease expires and the coordinator requeues its snapshot.
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
    finally:
        stop.set()
        if heartbeat.ident is not None:
            heartbeat.join(timeout=2)
        if temporary:
            shutil.rmtree(temporary, ignore_errors=True)
    return True


def run_worker(server: str, key: str, root: Path):
    parsed = urlsplit(server)
    if parsed.scheme != "https" and not (
        parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1", "::1"}
    ):
        raise ValueError("Remote workers require HTTPS; HTTP is allowed only on loopback.")
    if parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in {"", "/"}:
        raise ValueError("Worker server must be an origin without credentials or a path.")
    if len(key or "") < 32:
        raise ValueError(
            "Set OPENREEL_WORKER_KEY to the coordinator's dedicated key (at least 32 characters)."
        )
    worker_id = "remote-" + uid()
    with httpx.Client(
        base_url=server.rstrip("/"),
        headers={"Authorization": "Bearer " + key},
        timeout=30,
        follow_redirects=False,
    ) as client:
        try:
            while True:
                try:
                    if not run_once(client, root, worker_id):
                        time.sleep(2)
                except httpx.HTTPError as exc:
                    print(
                        f"Coordinator unavailable ({type(exc).__name__}); retrying in five seconds.",
                        flush=True,
                    )
                    time.sleep(5)
        except KeyboardInterrupt:
            return
