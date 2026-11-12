import io
import json
import time
import wave
from pathlib import Path

import imageio_ffmpeg
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from openreel.media import import_bytes
from openreel.models import Character, Scene
from openreel.remote import run_once
from openreel.service import Service
from openreel.web import create_app

WORKER_KEY = "worker-credential-for-tests-1234567890"
OWNER_KEY = "owner-credential-for-tests-1234567890"


@pytest.fixture
def remote_api(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENREEL_RENDER_WORKER", "0")
    monkeypatch.setenv("OPENREEL_WORKER_KEY", WORKER_KEY)
    service = Service(tmp_path / "coordinator")
    app = create_app(service, "https://reel.example.com", OWNER_KEY)
    with TestClient(
        app, base_url="https://reel.example.com", headers={"Authorization": "Bearer " + WORKER_KEY}
    ) as client:
        yield service, client
    service.close()


def add_job(service):
    project = service.create_project("Original snapshot")
    project.scenes = [Scene(duration=0.5, title_card="BEFORE", motion="still", transition="cut")]
    project = service.store.save(project)
    return project, service.renderer.submit(project.id, "draft")


def claim(client):
    response = client.post("/api/workers/claim", json={"worker_id": "test-worker"})
    assert response.status_code == 200, response.text
    job = response.json()["job"]
    return job, {"X-OpenReel-Lease": job["lease_token"]}


def test_dedicated_worker_credentials_are_scoped(remote_api):
    _, client = remote_api
    assert client.get("/api/projects").status_code == 401
    for authorization in ("", "Bearer wrong", "Bearer " + OWNER_KEY):
        response = client.post(
            "/api/workers/claim", json={"worker_id": "test"}, headers={"Authorization": authorization}
        )
        assert response.status_code == 401
    assert client.post("/api/workers/claim", json={"worker_id": "test"}).json() == {"job": None}


def test_assets_require_the_current_lease_and_snapshot_membership(remote_api):
    service, client = remote_api
    project, queued = add_job(service)
    image = io.BytesIO()
    Image.new("RGB", (32, 32), "red").save(image, "PNG")
    unrelated = import_bytes(service.store, project.id, "unused.png", image.getvalue())
    job, headers = claim(client)
    prefix = f"/api/workers/jobs/{job['id']}"
    assert client.get(prefix + "/assets/" + unrelated.id, headers=headers).status_code == 404
    assert client.get(prefix + "/assets/" + unrelated.id).status_code == 409
    assert client.post(prefix + "/heartbeat", headers=headers).json()["cancel"] is False
    assert client.post(prefix + "/heartbeat", headers={"X-OpenReel-Lease": "old"}).status_code == 409
    public = service.store.job(queued["id"])
    assert "lease_token" not in public and "project_snapshot" not in public


def test_stale_worker_cannot_complete_a_reassigned_job(remote_api):
    service, client = remote_api
    _, queued = add_job(service)
    job, old_headers = claim(client)
    service.store.update_job(queued["id"], lease_expires=time.time() - 1)
    replacement, _ = claim(client)
    assert replacement["lease_token"] != job["lease_token"]
    response = client.post(
        f"/api/workers/jobs/{job['id']}/complete", headers=old_headers, content=b"not relevant"
    )
    assert response.status_code == 409
    assert service.store.job(job["id"])["status"] == "rendering"
    assert not list((service.store.root / "renders").rglob("video.mp4"))


def test_cancellation_prevents_publication(remote_api):
    service, client = remote_api
    _, queued = add_job(service)
    job, headers = claim(client)
    service.renderer.cancel(queued["id"])
    prefix = f"/api/workers/jobs/{job['id']}"
    assert client.post(prefix + "/heartbeat", headers=headers).json()["cancel"] is True
    assert client.post(prefix + "/complete", headers=headers, content=b"discard").status_code == 409
    response = client.post(prefix + "/failed", headers=headers, json={"error": "Canceled remotely"})
    assert response.status_code == 200
    assert response.json()["status"] == "canceled"
    assert not list((service.store.root / "renders").rglob("video.mp4"))


def test_invalid_worker_media_never_becomes_an_export(remote_api):
    service, client = remote_api
    add_job(service)
    job, headers = claim(client)
    response = client.post(f"/api/workers/jobs/{job['id']}/complete", headers=headers, content=b"invalid mp4")
    assert response.status_code == 422
    assert service.store.job(job["id"])["status"] == "rendering"
    assert not list((service.store.root / "renders").rglob("video.mp4"))


def test_actual_remote_worker_roundtrip_keeps_original_snapshot(remote_api, tmp_path):
    service, client = remote_api
    project = service.create_project("Before submission")
    image = io.BytesIO()
    Image.new("RGB", (80, 60), "#467886").save(image, "PNG")
    visual = import_bytes(service.store, project.id, "shot.png", image.getvalue())
    voice = io.BytesIO()
    with wave.open(voice, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16000)
        wav.writeframes(b"\x00\x00" * 16000)
    audio = import_bytes(service.store, project.id, "voice.wav", voice.getvalue())
    reference = import_bytes(service.store, project.id, "reference.png", image.getvalue())
    project.characters = [Character(id="lead", name="Lead", reference_asset_id=reference.id)]
    project.scenes = [
        Scene(
            duration=0.5,
            title_card="REMOTE",
            subtitle="Saved snapshot",
            asset_id=visual.id,
            audio_asset_id=audio.id,
            character_ids=["lead"],
            transition="cut",
        )
    ]
    project = service.store.save(project)
    original = project.model_dump()
    job = service.renderer.submit(project.id, "draft")
    project.title = "Edited after submission"
    service.store.save(project)
    assert run_once(client, tmp_path / "worker-cache", "roundtrip") is True
    completed = service.store.job(job["id"])
    assert completed["status"] == "completed", completed.get("error")
    video = Path(completed["video_path"])
    reader = imageio_ffmpeg.read_frames(str(video))
    metadata = next(reader)
    assert metadata["size"] == (640, 360)
    assert 0.4 < metadata["duration"] < 0.7
    assert len(next(reader)) == 640 * 360 * 3
    reader.close()
    assert json.loads(video.with_name("project.json").read_text(encoding="utf-8")) == original
    owner_headers = {"Authorization": "Bearer " + OWNER_KEY}
    assert client.get(completed["video_url"], headers=owner_headers).status_code == 200
    assert "Saved snapshot" in client.get(completed["subtitles_url"], headers=owner_headers).text
    assert not list((tmp_path / "worker-cache").iterdir())
    assert run_once(client, tmp_path / "worker-cache", "roundtrip") is False
