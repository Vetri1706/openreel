import concurrent.futures
import threading
import time
from pathlib import Path

import imageio_ffmpeg
import pytest
from pydantic import ValidationError

from openreel.config import limits
from openreel.models import CaptionWord, Project, Scene
from openreel.render import Renderer, subtitles
from openreel.store import Conflict, Store


def queued(tmp_path):
    store = Store(tmp_path / "queue")
    project = store.create(Project(title="Queued", scenes=[Scene(duration=0.5)]))
    renderer = Renderer(store, autostart=False)
    job = renderer.submit(project.id, "draft")
    renderer.pool.shutdown()
    return store, project, job


def test_atomic_claim_and_private_leases(tmp_path):
    store, project, job = queued(tmp_path)
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        claims = list(pool.map(lambda i: Store(store.root).claim_job(f"worker-{i}"), range(8)))
    claimed = [value for value in claims if value]
    assert len(claimed) == 1
    assert claimed[0]["project_snapshot"]["revision"] == project.revision
    assert claimed[0]["lease_token"]
    assert "lease_token" not in store.job(job["id"])
    assert "project_snapshot" not in store.job(job["id"])
    assert store.heartbeat_job(job["id"], "wrong") is False
    with pytest.raises(Conflict):
        store.leased_job(job["id"], "wrong")


def test_expired_worker_cannot_publish_and_snapshot_survives(tmp_path):
    store, project, job = queued(tmp_path)
    first = store.claim_job("first")
    project.title = "Edited after submission"
    store.save(project)
    store.update_job(job["id"], lease_expires=time.time() - 1)
    second = Store(store.root).claim_job("replacement")
    assert second["project_snapshot"]["title"] == "Queued"
    assert second["attempts"] == 2
    assert not store.heartbeat_job(job["id"], first["lease_token"])
    with pytest.raises(Conflict):
        store.update_leased_job(job["id"], first["lease_token"], status="completed")
    assert store.heartbeat_job(job["id"], second["lease_token"])
    store.update_leased_job(job["id"], second["lease_token"], status="completed")
    assert store.job(job["id"])["status"] == "completed"


def test_canceled_queued_job_is_not_claimed(tmp_path):
    store, _, job = queued(tmp_path)
    assert store.cancel_job(job["id"])["status"] == "canceled"
    assert store.claim_job("worker") is None


def test_queue_capacity_counts_durable_jobs(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENREEL_MAX_QUEUED_RENDERS", "1")
    store, project, job = queued(tmp_path)
    renderer = Renderer(store, autostart=False)
    try:
        with pytest.raises(ValueError, match="queue is full"):
            renderer.submit(project.id, "draft")
        store.cancel_job(job["id"])
        assert renderer.submit(project.id, "draft")["status"] == "queued"
    finally:
        renderer.pool.shutdown()


def test_limits_are_configurable_and_allow_larger_projects(monkeypatch):
    assert Project(title="Longer", scenes=[Scene(duration=61) for _ in range(11)])
    assert limits().max_upload_bytes == 500 * 1024 * 1024
    monkeypatch.setenv("OPENREEL_MAX_SCENES", "2")
    with pytest.raises(ValidationError, match="at most 2 scenes"):
        Project(title="Three", scenes=[Scene() for _ in range(3)])
    monkeypatch.setenv("OPENREEL_MAX_DURATION_SECONDS", "5")
    with pytest.raises(ValidationError, match="at most 5 seconds"):
        Project(title="Long", scenes=[Scene(duration=6)])
    monkeypatch.setenv("OPENREEL_MAX_SCENE_SECONDS", "3")
    with pytest.raises(ValidationError, match="at most 3 seconds"):
        Scene(duration=4)
    monkeypatch.setenv("OPENREEL_MAX_UPLOAD_MB", "oops")
    with pytest.raises(ValueError, match="must be an integer"):
        limits()


def test_word_caption_timing_and_srt():
    words = [CaptionWord(text="Hello", start=0.1, end=0.4), CaptionWord(text="world", start=0.5, end=0.9)]
    project = Project(title="Words", scenes=[Scene(duration=1), Scene(duration=1, caption_words=words)])
    assert "00:00:01,100 --> 00:00:01,400\nHello" in subtitles(project)
    with pytest.raises(ValidationError, match="ordered"):
        Scene(duration=1, caption_words=list(reversed(words)))
    with pytest.raises(ValidationError, match="inside"):
        Scene(duration=0.5, caption_words=words)
    with pytest.raises(ValidationError):
        CaptionWord(text="x", start=0.5, end=0.4)


def test_worker_resumes_and_burns_word_captions(tmp_path):
    store = Store(tmp_path / "restart")
    project = store.create(
        Project(
            title="Word render",
            scenes=[
                Scene(
                    duration=1,
                    color="#222222",
                    motion="still",
                    transition="cut",
                    caption_words=[CaptionWord(text="TIMED", start=0.25, end=0.75)],
                )
            ],
        )
    )
    submitter = Renderer(store, autostart=False)
    job = submitter.submit(project.id, "draft")
    submitter.pool.shutdown()
    worker = Renderer(Store(store.root))
    try:
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline:
            state = store.job(job["id"])
            if state["status"] in {"completed", "failed"}:
                break
            time.sleep(0.1)
        assert state["status"] == "completed", state
        reader = imageio_ffmpeg.read_frames(state["video_path"])
        meta = next(reader)
        frames = list(reader)
        assert 0.9 <= meta["duration"] <= 1.1
        # A static background should only change while the timed word is visible.
        early = sum(frames[1])
        middle = sum(frames[12])
        late = sum(frames[22])
        assert abs(middle - early) > 10000
        assert abs(late - early) < 3000
        assert "TIMED" in Path(state["video_path"]).with_name("subtitles.srt").read_text()
    finally:
        worker.pool.shutdown()


def test_scratch_failure_releases_lease_without_heartbeat(tmp_path, monkeypatch):
    store, project, submitted = queued(tmp_path)
    claimed = store.claim_job("disk-full-worker")
    renderer = Renderer(store, autostart=False)

    def disk_full(**kwargs):
        raise OSError("No space left on device")

    monkeypatch.setattr("openreel.render.tempfile.mkdtemp", disk_full)
    try:
        renderer._render(project, claimed)
        state = store.job(submitted["id"])
        assert state["status"] == "failed"
        assert "No space left" in state["error"]
        assert not store.heartbeat_job(submitted["id"], claimed["lease_token"])
        assert not any(
            thread.name == f"openreel-heartbeat-{submitted['id'][:12]}" and thread.is_alive()
            for thread in threading.enumerate()
        )
    finally:
        renderer.pool.shutdown()
