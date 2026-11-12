"""Real ComfyUI generation and persistent handoffs to an agent's native media tools.

Only the server owner configures providers/workflows. A client submits a prompt,
never an executable workflow, URL, or local file path.
"""

import copy
import hashlib
import json
import os
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path, PurePosixPath
from typing import Literal
from urllib.parse import urlsplit

import httpx
from pydantic import Field, model_validator

from . import media
from .config import limits
from .models import Model, now, uid
from .store import Conflict, Store

TERMINAL = {"completed", "failed", "cancelled"}
LEASE_SECONDS = 90
MAX_CONFIG = 2 * 1024 * 1024


class GenerationRequest(Model):
    provider: Literal["agent", "comfyui"] = "agent"
    kind: Literal["image", "video"] = "image"
    prompt: str = Field(min_length=1, max_length=8000)
    seed: int = Field(default=42, ge=0, le=2**32 - 1)
    workflow_id: str | None = Field(default=None, max_length=80)
    reference_asset_id: str | None = None
    scene_id: str | None = None
    expected_revision: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def valid_request(self):
        if not self.prompt.strip():
            raise ValueError("Write a generation prompt first.")
        if self.scene_id and self.expected_revision is None:
            raise ValueError("Attaching generated media to a scene requires expected_revision.")
        if self.provider == "comfyui" and not self.workflow_id:
            raise ValueError("Choose a configured ComfyUI workflow.")
        if self.provider == "agent" and self.workflow_id:
            raise ValueError("An agent handoff does not use a ComfyUI workflow.")
        return self


class AgentProvenance(Model):
    producer: str | None = Field(default=None, max_length=200)
    model: str | None = Field(default=None, max_length=300)
    notes: str | None = Field(default=None, max_length=2000)


class GenerationManager:
    def __init__(self, store: Store, *, transport=None, poll_interval=2.0):
        self.store = store
        self.owner = uid()
        self.transport = transport
        self.poll_interval = poll_interval
        self.pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="openreel-generation")
        self.stopping = threading.Event()
        self.lock = threading.Lock()
        self.pending = set()
        with store.connect() as c:
            c.execute("CREATE TABLE IF NOT EXISTS generation_jobs (id TEXT PRIMARY KEY, data TEXT NOT NULL)")
        # Expired leases are recovered on startup and whenever clients poll jobs.
        self._recover()

    def _config(self):
        address = os.environ.get("OPENREEL_COMFYUI_URL", "").rstrip("/")
        path = os.environ.get("OPENREEL_COMFYUI_WORKFLOWS")
        if not address or not path:
            return address, {}
        parsed = urlsplit(address)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or any((parsed.username, parsed.password, parsed.query, parsed.fragment))
        ):
            raise ValueError("OPENREEL_COMFYUI_URL must be an HTTP(S) server address without credentials.")
        file = Path(path)
        if file.stat().st_size > MAX_CONFIG:
            raise ValueError("ComfyUI workflow configuration exceeds 2 MB.")
        workflows = json.loads(file.read_text(encoding="utf-8"))
        if not isinstance(workflows, dict) or len(workflows) > 100:
            raise ValueError("ComfyUI workflows must be an object of at most 100 named workflows.")
        for key, workflow in workflows.items():
            if not isinstance(key, str) or not key or len(key) > 80 or not isinstance(workflow, dict):
                raise ValueError("Each workflow needs a short ID and a configuration object.")
            if workflow.get("kind") not in {"image", "video"}:
                raise ValueError(f"Workflow {key} needs kind image or video.")
            graph = workflow.get("prompt")
            bindings = workflow.get("bindings", {})
            if not isinstance(graph, dict) or not graph or not isinstance(bindings, dict):
                raise ValueError(f"Workflow {key} needs an API-format prompt and bindings.")
            if any(
                not isinstance(node, dict)
                or not isinstance(node.get("class_type"), str)
                or not isinstance(node.get("inputs"), dict)
                for node in graph.values()
            ):
                raise ValueError(f"Workflow {key} must contain API-format nodes with inputs.")
            if "prompt" not in bindings:
                raise ValueError(f"Workflow {key} needs a prompt binding.")
            for binding, target in bindings.items():
                if (
                    binding not in {"prompt", "seed", "reference"}
                    or not isinstance(target, list)
                    or len(target) != 2
                ):
                    raise ValueError(f"Workflow {key} has an invalid input binding.")
                node, name = target
                if (
                    not isinstance(node, str)
                    or not isinstance(name, str)
                    or name not in graph.get(node, {}).get("inputs", {})
                ):
                    raise ValueError(f"Workflow {key} binds an unknown node input.")
            if str(workflow.get("output_node")) not in graph:
                raise ValueError(f"Workflow {key} needs an output_node from its prompt graph.")
            if workflow.get("output_key", "images") not in {"images", "videos", "gifs"}:
                raise ValueError(f"Workflow {key} has an unsupported output_key.")
        return address, workflows

    def capabilities(self) -> dict:
        error = None
        try:
            address, workflows = self._config()
        except (OSError, ValueError, TypeError) as exc:
            address, workflows, error = "", {}, f"Invalid ComfyUI configuration: {type(exc).__name__}."
        return {
            "providers": [
                {
                    "id": "agent",
                    "name": "Connected AI agent",
                    "kinds": ["image", "video"],
                    "configured": True,
                    "ready": False,
                    "mode": "handoff",
                    "workflows": [],
                    "description": "An agent with native media tools must generate and upload the actual output.",
                },
                {
                    "id": "comfyui",
                    "name": "ComfyUI",
                    "kinds": sorted({w["kind"] for w in workflows.values()}),
                    "configured": bool(address and workflows),
                    "ready": False,
                    "mode": "provider",
                    "description": "Configured workflows are checked against the running server before generation.",
                    "error": error,
                    "workflows": [
                        {
                            "id": key,
                            "name": value.get("name", key),
                            "kind": value["kind"],
                            "requires_reference": "reference" in value.get("bindings", {}),
                        }
                        for key, value in workflows.items()
                    ],
                },
            ],
            "native_agent_handoff": True,
            "readiness": "Provider availability is validated when a job starts; agent tools belong to the client.",
        }

    def _raw(self, job_id):
        with self.store.connect() as c:
            row = c.execute("SELECT data FROM generation_jobs WHERE id=?", (job_id,)).fetchone()
        if not row:
            raise KeyError("Generation job not found")
        return json.loads(row[0])

    @staticmethod
    def _public(job):
        return {
            key: value
            for key, value in job.items()
            if key not in {"lease_owner", "lease_until", "workflow", "backend_url"}
        }

    def get(self, job_id):
        job = self._raw(job_id)
        if (
            job["status"] in {"queued", "generating", "finalizing"}
            and job.get("lease_until", 0) < time.time()
        ):
            self._schedule(job_id)
        return self._public(job)

    def list(self, project_id=None, status=None):
        if project_id:
            self.store.get(project_id)
        if status and status not in TERMINAL | {"queued", "generating", "finalizing", "awaiting_agent"}:
            raise ValueError("Unknown generation status.")
        with self.store.connect() as c:
            rows = c.execute("SELECT data FROM generation_jobs ORDER BY rowid DESC").fetchall()
        return [
            self.get(job["id"])
            for row in rows
            if (job := json.loads(row[0]))
            and (not project_id or job["project_id"] == project_id)
            and (not status or job["status"] == status)
        ]

    def _change(self, job_id, *, allowed=None, owner=None, **changes):
        with self.store.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            row = c.execute("SELECT data FROM generation_jobs WHERE id=?", (job_id,)).fetchone()
            if not row:
                raise KeyError("Generation job not found")
            job = json.loads(row[0])
            if (allowed is not None and job["status"] not in allowed) or (
                owner and job.get("lease_owner") != owner
            ):
                return None
            job.update(changes, updated_at=now())
            c.execute("UPDATE generation_jobs SET data=? WHERE id=?", (json.dumps(job), job_id))
        return job

    def submit(self, project_id: str, request: GenerationRequest):
        request = GenerationRequest.model_validate(request)
        project = self.store.get(project_id)
        if request.scene_id:
            if request.expected_revision != project.revision:
                raise Conflict("Project changed. Reload before requesting generation.")
            if not any(scene.id == request.scene_id for scene in project.scenes):
                raise KeyError("Scene not found")
        if request.reference_asset_id:
            reference = self.store.asset(request.reference_asset_id)
            if reference.project_id != project_id or reference.kind != "image":
                raise ValueError("The reference must be an image belonging to this project.")
        workflow = None
        if request.provider == "comfyui":
            address, workflows = self._config()
            if not address or request.workflow_id not in workflows:
                raise ValueError("Configure the requested ComfyUI workflow on the server first.")
            workflow = copy.deepcopy(workflows[request.workflow_id])
            if workflow["kind"] != request.kind:
                raise ValueError("The workflow produces a different media kind.")
            needs_reference = "reference" in workflow["bindings"]
            if needs_reference != bool(request.reference_asset_id):
                raise ValueError(
                    "This workflow requires a reference image."
                    if needs_reference
                    else "This workflow does not accept a reference image."
                )
        job = {
            "id": uid(),
            "project_id": project_id,
            "status": "awaiting_agent" if request.provider == "agent" else "queued",
            "request": request.model_dump(),
            "created_at": now(),
            "updated_at": now(),
            "asset_id": None,
            "attached": False,
            "error": None,
            "provenance": {},
            "workflow": workflow,
            "lease_owner": None,
            "lease_until": 0,
            "provider_job_id": None,
            "backend_url": address if request.provider == "comfyui" else None,
        }
        if request.provider == "agent":
            job["instructions"] = (
                "Generate the requested media using your native tools, upload the real file to this project, then complete_generation with its asset_id. If your client cannot generate this media, explain that limitation; never upload a placeholder as generated output."
            )
        with self.store.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            active = sum(
                json.loads(row[0])["status"] not in TERMINAL
                for row in c.execute("SELECT data FROM generation_jobs")
            )
            if active >= 32:
                raise ValueError("Generation queue is full. Complete or cancel a pending request first.")
            c.execute("INSERT INTO generation_jobs VALUES (?, ?)", (job["id"], json.dumps(job)))
        if request.provider == "comfyui":
            self._schedule(job["id"])
        return self._public(job)

    def _schedule(self, job_id):
        with self.lock:
            if self.stopping.is_set() or job_id in self.pending:
                return
            self.pending.add(job_id)
            self.pool.submit(self._run, job_id)

    def _recover(self):
        with self.store.connect() as c:
            rows = c.execute("SELECT data FROM generation_jobs").fetchall()
        for row in rows:
            job = json.loads(row[0])
            if (
                job["status"] in {"queued", "generating", "finalizing"}
                and job.get("lease_until", 0) < time.time()
            ):
                self._schedule(job["id"])

    def _claim(self, job_id):
        with self.store.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            job = json.loads(
                c.execute("SELECT data FROM generation_jobs WHERE id=?", (job_id,)).fetchone()[0]
            )
            if (
                job["status"] not in {"queued", "generating", "finalizing"}
                or job.get("lease_until", 0) >= time.time()
            ):
                return None
            if job["status"] == "generating" and not job.get("provider_job_id"):
                job.update(
                    status="failed",
                    error="Generation was interrupted while submitting. Check ComfyUI before retrying to avoid duplicate work.",
                )
            else:
                job.update(lease_owner=self.owner, lease_until=time.time() + LEASE_SECONDS)
            c.execute("UPDATE generation_jobs SET data=? WHERE id=?", (json.dumps(job), job_id))
        return job if job["status"] not in TERMINAL else None

    def _client(self, address):
        token = os.environ.get("OPENREEL_COMFYUI_TOKEN")
        return httpx.Client(
            base_url=address + "/",
            timeout=30,
            follow_redirects=False,
            trust_env=False,
            headers={"Authorization": "Bearer " + token} if token else {},
            transport=self.transport,
        )

    @staticmethod
    def _json(client, method, path, **kwargs):
        with client.stream(method, path, **kwargs) as response:
            response.raise_for_status()
            data = bytearray()
            for chunk in response.iter_bytes(chunk_size=64 * 1024):
                if len(data) + len(chunk) > 8 * 1024 * 1024:
                    raise ValueError("ComfyUI returned an oversized API response.")
                data.extend(chunk)
        return json.loads(data)

    def _validate_graph(self, graph, info, reference_binding):
        if not isinstance(info, dict):
            raise ValueError("ComfyUI returned invalid node information.")
        for node_id, node in graph.items():
            if not isinstance(node, dict) or node.get("class_type") not in info:
                raise ValueError(f"ComfyUI is missing the node class for workflow node {node_id}.")
            node_info = info[node["class_type"]]
            specs = {
                **node_info.get("input", {}).get("required", {}),
                **node_info.get("input", {}).get("optional", {}),
            }
            # Dropdowns include checkpoints, VAEs and LoRAs. Reject unavailable configured models.
            for name, value in node.get("inputs", {}).items():
                spec = specs.get(name)
                if reference_binding == [node_id, name]:
                    continue  # This image is uploaded immediately after validation.
                if (
                    spec
                    and isinstance(spec[0], list)
                    and not isinstance(value, list)
                    and value not in spec[0]
                ):
                    raise ValueError(f"ComfyUI node {node_id} has an unavailable model or option for {name}.")

    def _submit_comfy(self, client, job):
        request, workflow = job["request"], job["workflow"]
        graph = copy.deepcopy(workflow["prompt"])
        for name in ("prompt", "seed"):
            if name in workflow["bindings"]:
                node, field = workflow["bindings"][name]
                graph[node]["inputs"][field] = request[name]
        info = self._json(client, "GET", "object_info")
        self._validate_graph(graph, info, workflow["bindings"].get("reference"))
        if request["reference_asset_id"]:
            reference = self.store.asset(request["reference_asset_id"])
            with self.store.asset_path(reference.id).open("rb") as file:
                uploaded = self._json(
                    client,
                    "POST",
                    "upload/image",
                    files={"image": ("openreel-" + reference.filename, file, "image/png")},
                    data={"type": "input", "overwrite": "false"},
                )
            name = uploaded.get("name", "")
            subfolder = uploaded.get("subfolder", "")
            self._safe_descriptor(name, subfolder)
            node, field = workflow["bindings"]["reference"]
            graph[node]["inputs"][field] = str(PurePosixPath(subfolder) / name)
        provenance = {
            "provider": "comfyui",
            "workflow_id": request["workflow_id"],
            "model": str(workflow.get("model", "workflow-configured"))[:300],
            "seed": request["seed"] if "seed" in workflow["bindings"] else None,
            "requested_seed": request["seed"],
            "prompt": request["prompt"],
            "reference_asset_id": request["reference_asset_id"],
            "workflow_sha256": hashlib.sha256(json.dumps(graph, sort_keys=True).encode()).hexdigest(),
        }
        if not self._change(
            job["id"],
            owner=self.owner,
            allowed={"queued"},
            status="generating",
            provenance=provenance,
            lease_until=time.time() + LEASE_SECONDS,
        ):
            return None
        response = self._json(client, "POST", "prompt", json={"prompt": graph, "client_id": job["id"]})
        prompt_id = response.get("prompt_id")
        if (
            not isinstance(prompt_id, str)
            or not prompt_id
            or len(prompt_id) > 128
            or any(c not in "0123456789abcdefABCDEF-_" for c in prompt_id)
        ):
            raise ValueError("ComfyUI did not return a valid prompt ID.")
        # Keep the provider ID even when cancellation raced with submission.
        self._change(
            job["id"], owner=self.owner, provider_job_id=prompt_id, lease_until=time.time() + LEASE_SECONDS
        )
        return prompt_id

    @staticmethod
    def _safe_descriptor(filename, subfolder):
        if (
            not isinstance(filename, str)
            or not filename
            or len(filename) > 255
            or any(c in filename for c in "/\\\x00")
        ):
            raise ValueError("ComfyUI returned an unsafe output filename.")
        if not isinstance(subfolder, str) or len(subfolder) > 512 or "\\" in subfolder or "\x00" in subfolder:
            raise ValueError("ComfyUI returned an unsafe output folder.")
        folder = PurePosixPath(subfolder)
        if folder.is_absolute() or ".." in folder.parts or ":" in subfolder:
            raise ValueError("ComfyUI returned an unsafe output folder.")

    def _download(self, client, job, history):
        workflow = job["workflow"]
        outputs = history.get("outputs", {}).get(str(workflow["output_node"]), {})
        descriptors = outputs.get(workflow.get("output_key", "images"), [])
        extensions = (
            {".png", ".jpg", ".jpeg", ".webp"}
            if job["request"]["kind"] == "image"
            else {".mp4", ".webm", ".mov"}
        )
        for descriptor in descriptors:
            filename, subfolder = descriptor.get("filename", ""), descriptor.get("subfolder", "")
            self._safe_descriptor(filename, subfolder)
            if Path(filename).suffix.lower() not in extensions or descriptor.get("type") != "output":
                continue
            temporary = None
            maximum = limits().max_upload_bytes
            try:
                with tempfile.NamedTemporaryFile(
                    dir=self.store.root / "scratch", delete=False
                ) as destination:
                    temporary = Path(destination.name)
                    with client.stream(
                        "GET", "view", params={"filename": filename, "subfolder": subfolder, "type": "output"}
                    ) as response:
                        response.raise_for_status()
                        total, refreshed = 0, 0
                        for chunk in response.iter_bytes(chunk_size=1024 * 1024):
                            if self.stopping.is_set():
                                return None
                            if time.monotonic() - refreshed >= 10:
                                if not self._change(
                                    job["id"],
                                    owner=self.owner,
                                    allowed={"generating"},
                                    lease_until=time.time() + LEASE_SECONDS,
                                ):
                                    return None
                                refreshed = time.monotonic()
                            total += len(chunk)
                            if total > maximum:
                                raise ValueError("Generated media exceeds the configured import limit.")
                            destination.write(chunk)
                if not self._change(
                    job["id"],
                    owner=self.owner,
                    allowed={"generating"},
                    lease_until=time.time() + LEASE_SECONDS,
                ):
                    return None
                asset = media.import_file(self.store, job["project_id"], filename, temporary)
                if asset.kind != job["request"]["kind"]:
                    raise ValueError("ComfyUI returned the wrong media kind.")
                return asset
            finally:
                if temporary:
                    temporary.unlink(missing_ok=True)
        raise ValueError(
            "The configured output node did not produce a supported media file of the requested kind."
        )

    def _run(self, job_id):
        owned = False
        try:
            job = self._claim(job_id)
            if not job:
                return
            owned = True
            if job["status"] == "finalizing":
                self._finish(job)
                return
            address = job.get("backend_url")
            if not address:
                raise ValueError("Configure OPENREEL_COMFYUI_URL before generating media.")
            with self._client(address) as client:
                prompt_id = job.get("provider_job_id") or self._submit_comfy(client, job)
                if not prompt_id:
                    return
                deadline = time.monotonic() + 3600
                while not self.stopping.is_set():
                    current = self._raw(job_id)
                    if current["status"] == "cancelled":
                        self._delete_pending(client, prompt_id)
                        return
                    if time.monotonic() > deadline:
                        raise ValueError(
                            "ComfyUI did not finish within one hour. Check its queue before retrying."
                        )
                    if not self._change(
                        job_id,
                        owner=self.owner,
                        allowed={"generating"},
                        lease_until=time.time() + LEASE_SECONDS,
                    ):
                        if self._raw(job_id)["status"] == "cancelled":
                            self._delete_pending(client, prompt_id)
                        return
                    history = self._json(client, "GET", "history/" + prompt_id).get(prompt_id)
                    if history:
                        status = history.get("status", {})
                        if status.get("status_str") == "error":
                            raise ValueError(
                                "ComfyUI could not execute the workflow. Check its server logs and installed models."
                            )
                        if status.get("completed") is True:
                            asset = self._download(client, current, history)
                            if asset:
                                final = self._change(
                                    job_id,
                                    owner=self.owner,
                                    allowed={"generating"},
                                    status="finalizing",
                                    asset_id=asset.id,
                                )
                                if final:
                                    self._finish(final)
                            return
                    self.stopping.wait(self.poll_interval)
        except Exception as exc:
            # Provider responses can contain credentials or host paths; expose a bounded safe error.
            message = (
                str(exc)
                if isinstance(exc, (ValueError, Conflict))
                else f"Generation failed ({type(exc).__name__}). Check provider connectivity and configuration."
            )
            self._change(
                job_id,
                owner=self.owner,
                allowed={"queued", "generating", "finalizing"},
                status="failed",
                error=message[:1000],
                lease_until=0,
            )
        finally:
            if owned:
                self._change(
                    job_id, owner=self.owner, allowed={"queued", "generating", "finalizing"}, lease_until=0
                )
            with self.lock:
                self.pending.discard(job_id)

    @staticmethod
    def _delete_pending(client, prompt_id):
        try:
            client.post("queue", json={"delete": [prompt_id]}).raise_for_status()
        except httpx.HTTPError:
            pass

    def cancel(self, job_id):
        job = self._change(
            job_id,
            allowed={"queued", "generating", "awaiting_agent"},
            status="cancelled",
            lease_until=0,
            cancellation_note="OpenReel will not attach output. A running provider workflow may continue; no global ComfyUI interrupt is sent.",
        )
        return self._public(job or self._raw(job_id))

    def complete(self, job_id, asset_id, provenance=None):
        job = self._raw(job_id)
        if job["request"]["provider"] != "agent":
            raise ValueError("Only agent handoffs accept supplied output assets.")
        if job["status"] == "completed" and job["asset_id"] == asset_id:
            return self._public(job)
        asset = self.store.asset(asset_id)
        if asset.project_id != job["project_id"] or asset.kind != job["request"]["kind"]:
            raise ValueError(
                "Generated output must belong to this project and match the requested media kind."
            )
        provenance = AgentProvenance.model_validate(provenance or {}).model_dump(exclude_none=True)
        recorded = {
            **provenance,
            "provider": "agent",
            "source": "agent-reported",
            "prompt": job["request"]["prompt"],
            "requested_seed": job["request"]["seed"],
            "reference_asset_id": job["request"]["reference_asset_id"],
        }
        final = self._change(
            job_id,
            allowed={"awaiting_agent"},
            status="finalizing",
            asset_id=asset_id,
            provenance=recorded,
            lease_owner=self.owner,
            lease_until=time.time() + LEASE_SECONDS,
        )
        if not final:
            raise Conflict("Generation request is no longer awaiting agent output.")
        return self._public(self._finish(final))

    def _finish(self, job):
        if not self._change(
            job["id"], owner=self.owner, allowed={"finalizing"}, lease_until=time.time() + LEASE_SECONDS
        ):
            return self._raw(job["id"])
        attached, attachment_error = False, None
        request = job["request"]
        if request["scene_id"]:
            project = self.store.get(job["project_id"])
            scene = next((scene for scene in project.scenes if scene.id == request["scene_id"]), None)
            if scene and scene.asset_id == job["asset_id"]:
                attached = True
            elif not scene or project.revision != request["expected_revision"]:
                attachment_error = "The project changed during generation. The output is in the media library; attach it after reviewing the current scene."
            else:
                scene.asset_id = job["asset_id"]
                try:
                    self.store.save(project)
                    attached = True
                except Conflict:
                    attachment_error = "The project changed during generation. The output is in the media library; attach it after reviewing the current scene."
        return self._change(
            job["id"],
            owner=self.owner,
            allowed={"finalizing"},
            status="completed",
            attached=attached,
            attachment_error=attachment_error,
            lease_until=0,
        )

    def shutdown(self, wait=True):
        self.stopping.set()
        self.pool.shutdown(wait=wait)
