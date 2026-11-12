import io
import json
import subprocess
import threading
import time
from contextlib import contextmanager
from types import SimpleNamespace

import httpx
import pytest
from PIL import Image

from openreel import media
from openreel.generation import GenerationManager, GenerationRequest
from openreel.models import Project, Scene
from openreel.store import Conflict, Store


def png():
    output = io.BytesIO()
    Image.new("RGB", (32, 24), "purple").save(output, "PNG")
    return output.getvalue()


def configure(monkeypatch, tmp_path, *, kind="image", reference=False):
    workflow = {
        "kind": kind,
        "model": "installed-test-model",
        "prompt": {
            "1": {"class_type": "Text", "inputs": {"text": "initial"}},
            "2": {"class_type": "Checkpoint", "inputs": {"model": "installed.safetensors"}},
            "3": {"class_type": "Sampler", "inputs": {"seed": 0, "text": ["1", 0], "model": ["2", 0]}},
            "4": {"class_type": "Save", "inputs": {"images": ["3", 0]}},
        },
        "bindings": {"prompt": ["1", "text"], "seed": ["3", "seed"]},
        "output_node": "4",
        "output_key": "images" if kind == "image" else "gifs",
    }
    if reference:
        workflow["prompt"]["5"] = {"class_type": "LoadImage", "inputs": {"image": "placeholder.png"}}
        workflow["bindings"]["reference"] = ["5", "image"]
    config = tmp_path / "workflows.json"
    config.write_text(json.dumps({"test": workflow}))
    monkeypatch.setenv("OPENREEL_COMFYUI_URL", "http://comfy.test:8188")
    monkeypatch.setenv("OPENREEL_COMFYUI_WORKFLOWS", str(config))
    return workflow


class Backend:
    def __init__(self, *, output=None, filename="output.png", output_key="images"):
        self.output = output if output is not None else png()
        self.filename = filename
        self.output_key = output_key
        self.submissions = []
        self.uploads = 0
        self.deleted = []
        self.finished = True
        self.fail = False
        self.models = ["installed.safetensors"]
        self.submitted = threading.Event()

    def handle(self, request):
        path = request.url.path
        if path == "/object_info":
            return httpx.Response(
                200,
                json={
                    "Text": {"input": {"required": {"text": ["STRING"]}}},
                    "Checkpoint": {"input": {"required": {"model": [self.models]}}},
                    "Sampler": {"input": {"required": {"seed": ["INT"]}}},
                    "Save": {"input": {}},
                    "LoadImage": {"input": {"required": {"image": [["placeholder.png"]]}}},
                },
            )
        if path == "/upload/image":
            self.uploads += 1
            return httpx.Response(
                200, json={"name": "uploaded.png", "subfolder": "openreel", "type": "input"}
            )
        if path == "/prompt":
            self.submissions.append(json.loads(request.content))
            self.submitted.set()
            return httpx.Response(200, json={"prompt_id": "abc123"})
        if path == "/history/abc123":
            if not self.finished:
                return httpx.Response(200, json={})
            return httpx.Response(
                200,
                json={
                    "abc123": {
                        "status": {"completed": True, "status_str": "error" if self.fail else "success"},
                        "outputs": {
                            "4": {
                                self.output_key: [
                                    {"filename": self.filename, "subfolder": "", "type": "output"}
                                ]
                            }
                        },
                    }
                },
            )
        if path == "/view":
            return httpx.Response(200, content=self.output)
        if path == "/queue":
            self.deleted.extend(json.loads(request.content)["delete"])
            return httpx.Response(200, json={})
        raise AssertionError(f"Unexpected request: {request.method} {request.url}")


@contextmanager
def manager(store, backend=None):
    instance = GenerationManager(
        store, transport=httpx.MockTransport(backend.handle) if backend else None, poll_interval=0.01
    )
    try:
        yield instance
    finally:
        instance.shutdown()


def finished(instance, job):
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        result = instance.get(job["id"])
        if result["status"] in {"completed", "failed", "cancelled"}:
            return result
        time.sleep(0.01)
    raise AssertionError(instance.get(job["id"]))


def test_agent_handoff_survives_restart_and_attaches(tmp_path):
    store = Store(tmp_path / "data")
    project = store.create(Project(title="Agent", scenes=[Scene(id="shot")]))
    with manager(store) as first:
        job = first.submit(
            project.id, GenerationRequest(prompt="A character portrait", scene_id="shot", expected_revision=1)
        )
        assert job["status"] == "awaiting_agent"
        assert not job["asset_id"]
    with manager(Store(store.root)) as second:
        pending = second.list(status="awaiting_agent")
        assert pending[0]["id"] == job["id"]
        asset = media.import_bytes(store, project.id, "portrait.png", png())
        complete = second.complete(
            job["id"], asset.id, {"producer": "native-agent", "model": "client-reported"}
        )
        assert complete["status"] == "completed"
        assert complete["attached"]
        assert complete["provenance"]["source"] == "agent-reported"
        assert store.get(project.id).scenes[0].asset_id == asset.id
        assert second.complete(job["id"], asset.id) == complete


def test_agent_scope_kind_revision_and_cancel(tmp_path):
    store = Store(tmp_path / "data")
    project = store.create(Project(title="Agent", scenes=[Scene(id="shot")]))
    other = store.create(Project(title="Other"))
    with manager(store) as instance:
        job = instance.submit(
            project.id, GenerationRequest(prompt="A portrait", scene_id="shot", expected_revision=1)
        )
        alien = media.import_bytes(store, other.id, "wrong.png", png())
        with pytest.raises(ValueError, match="belong"):
            instance.complete(job["id"], alien.id)
        store.save(project)
        asset = media.import_bytes(store, project.id, "right.png", png())
        result = instance.complete(job["id"], asset.id)
        assert result["status"] == "completed" and not result["attached"]
        assert "changed" in result["attachment_error"]
        assert store.get(project.id).scenes[0].asset_id is None
        cancelled = instance.submit(project.id, GenerationRequest(prompt="Another portrait"))
        assert instance.cancel(cancelled["id"])["status"] == "cancelled"
        with pytest.raises(Conflict):
            instance.complete(cancelled["id"], asset.id)
        video = instance.submit(project.id, GenerationRequest(prompt="A video", kind="video"))
        with pytest.raises(ValueError, match="kind"):
            instance.complete(video["id"], asset.id)


def test_comfy_real_image_import_and_provenance(monkeypatch, tmp_path):
    configure(monkeypatch, tmp_path)
    store = Store(tmp_path / "data")
    project = store.create(Project(title="Comfy", scenes=[Scene(id="shot")]))
    backend = Backend()
    with manager(store, backend) as instance:
        assert instance.capabilities()["providers"][1]["configured"]
        job = instance.submit(
            project.id,
            GenerationRequest(
                provider="comfyui",
                workflow_id="test",
                prompt="An owl",
                seed=17,
                scene_id="shot",
                expected_revision=1,
            ),
        )
        result = finished(instance, job)
        assert result["status"] == "completed", result
        assert result["attached"]
        assert store.asset(result["asset_id"]).kind == "image"
        graph = backend.submissions[0]["prompt"]
        assert graph["1"]["inputs"]["text"] == "An owl"
        assert graph["3"]["inputs"]["seed"] == 17
        assert len(result["provenance"]["workflow_sha256"]) == 64
        assert result["provider_job_id"] == "abc123"
        assert "workflow" not in result and "backend_url" not in result


def test_comfy_reference_video(monkeypatch, tmp_path):
    configure(monkeypatch, tmp_path, kind="video", reference=True)
    video = tmp_path / "clip.mp4"
    subprocess.run(
        [
            media.ffmpeg(),
            "-nostdin",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=red:s=32x32:r=24",
            "-t",
            "0.2",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(video),
        ],
        check=True,
    )
    store = Store(tmp_path / "data")
    project = store.create(Project(title="Video"))
    reference = media.import_bytes(store, project.id, "start.png", png())
    backend = Backend(output=video.read_bytes(), filename="clip.mp4", output_key="gifs")
    with manager(store, backend) as instance:
        job = instance.submit(
            project.id,
            GenerationRequest(
                provider="comfyui",
                workflow_id="test",
                kind="video",
                prompt="Move the camera",
                reference_asset_id=reference.id,
            ),
        )
        result = finished(instance, job)
        assert result["status"] == "completed", result
        assert store.asset(result["asset_id"]).kind == "video"
        assert backend.uploads == 1
        assert backend.submissions[0]["prompt"]["5"]["inputs"]["image"] == "openreel/uploaded.png"


@pytest.mark.parametrize("failure", ["model", "execution", "invalid_media", "unsafe_path", "oversized"])
def test_provider_failures_never_complete_or_attach(monkeypatch, tmp_path, failure):
    configure(monkeypatch, tmp_path)
    store = Store(tmp_path / "data")
    project = store.create(Project(title="Failure", scenes=[Scene(id="shot")]))
    backend = Backend()
    if failure == "model":
        backend.models = ["different.safetensors"]
    elif failure == "execution":
        backend.fail = True
    elif failure == "invalid_media":
        backend.output = b"not an image"
    elif failure == "unsafe_path":
        backend.filename = "../../secret.png"
    else:
        monkeypatch.setattr("openreel.generation.limits", lambda: SimpleNamespace(max_upload_bytes=10))
    with manager(store, backend) as instance:
        job = instance.submit(
            project.id,
            GenerationRequest(
                provider="comfyui", workflow_id="test", prompt="Owl", scene_id="shot", expected_revision=1
            ),
        )
        result = finished(instance, job)
        assert result["status"] == "failed", result
        assert result["asset_id"] is None
        assert not store.assets(project.id)
        assert not list((store.root / "scratch").iterdir())
        assert store.get(project.id).scenes[0].asset_id is None
        if failure == "model":
            assert not backend.submissions


def test_cancel_only_deletes_its_prompt(monkeypatch, tmp_path):
    configure(monkeypatch, tmp_path)
    store = Store(tmp_path / "data")
    project = store.create(Project(title="Cancel"))
    backend = Backend()
    backend.finished = False
    with manager(store, backend) as instance:
        job = instance.submit(
            project.id, GenerationRequest(provider="comfyui", workflow_id="test", prompt="Owl")
        )
        assert backend.submitted.wait(3)
        assert instance.cancel(job["id"])["status"] == "cancelled"
        deadline = time.monotonic() + 3
        while not backend.deleted and time.monotonic() < deadline:
            time.sleep(0.01)
        assert backend.deleted == ["abc123"]
        backend.finished = True
        assert instance.get(job["id"])["status"] == "cancelled"
        assert not store.assets(project.id)


def test_resume_known_provider_job_without_resubmission(monkeypatch, tmp_path):
    configure(monkeypatch, tmp_path)
    store = Store(tmp_path / "data")
    project = store.create(Project(title="Resume"))
    backend = Backend()
    backend.finished = False
    with manager(store, backend) as first:
        job = first.submit(
            project.id, GenerationRequest(provider="comfyui", workflow_id="test", prompt="Owl")
        )
        assert backend.submitted.wait(3)
    backend.finished = True
    with manager(Store(store.root), backend) as second:
        result = finished(second, job)
        assert result["status"] == "completed", result
        assert len(backend.submissions) == 1


def test_competing_worker_cannot_release_another_lease(tmp_path):
    store = Store(tmp_path / "data")
    project = store.create(Project(title="Lease"))
    with manager(store) as instance:
        job = instance.submit(project.id, GenerationRequest(prompt="Owl"))
        lease = time.time() + 1000
        instance._change(job["id"], status="generating", lease_owner="another-worker", lease_until=lease)
        instance._run(job["id"])
        assert instance._raw(job["id"])["lease_until"] == lease
        assert instance._raw(job["id"])["lease_owner"] == "another-worker"


def test_capabilities_do_not_claim_native_tools_or_unconfigured_provider(monkeypatch, tmp_path):
    monkeypatch.delenv("OPENREEL_COMFYUI_URL", raising=False)
    monkeypatch.delenv("OPENREEL_COMFYUI_WORKFLOWS", raising=False)
    store = Store(tmp_path / "data")
    with manager(store) as instance:
        agent, comfy = instance.capabilities()["providers"]
        assert agent["mode"] == "handoff" and not agent["ready"]
        assert not comfy["configured"] and not comfy["ready"]


def test_api_response_size_is_bounded(tmp_path):
    store = Store(tmp_path / "data")
    transport = httpx.MockTransport(lambda request: httpx.Response(200, content=b" " * (8 * 1024 * 1024 + 1)))
    with (
        manager(store) as instance,
        httpx.Client(transport=transport, base_url="http://comfy.test") as client,
    ):
        with pytest.raises(ValueError, match="oversized API"):
            instance._json(client, "GET", "/object_info")


def test_invalid_configuration_is_reported_without_claiming_availability(monkeypatch, tmp_path):
    configure(monkeypatch, tmp_path)
    config = tmp_path / "workflows.json"
    config.write_text(
        json.dumps({"broken": {"kind": "image", "prompt": {"1": []}, "bindings": {"prompt": ["1", "text"]}}})
    )
    with manager(Store(tmp_path / "data")) as instance:
        provider = instance.capabilities()["providers"][1]
        assert not provider["configured"]
        assert provider["error"]


def test_generation_download_streams_to_file_and_cleans_scratch(monkeypatch, tmp_path):
    configure(monkeypatch, tmp_path)
    store = Store(tmp_path / "data")
    project = store.create(Project(title="Streaming download"))
    backend = Backend()
    original_import = media.import_file
    seen = []

    def import_download(store, project_id, filename, source):
        assert source.parent == store.root / "scratch"
        assert source.is_file()
        assert source.read_bytes() == backend.output
        seen.append(source)
        return original_import(store, project_id, filename, source)

    def reject_buffered_import(*args):
        raise AssertionError("Provider downloads must use disk-backed import_file")

    monkeypatch.setattr(media, "import_file", import_download)
    monkeypatch.setattr(media, "import_bytes", reject_buffered_import)
    with manager(store, backend) as instance:
        job = instance.submit(
            project.id, GenerationRequest(provider="comfyui", workflow_id="test", prompt="Owl")
        )
        result = finished(instance, job)
        assert result["status"] == "completed", result
        assert len(seen) == 1
        assert not seen[0].exists()
        assert not list((store.root / "scratch").iterdir())
