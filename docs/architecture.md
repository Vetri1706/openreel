# Architecture and extension points

The project is intentionally a single Python package. The browser has no build
step and shares its application service with MCP. This keeps first installation
small and makes the core usable without a browser or Node.js.

## Ownership of responsibilities

- `models.py` rejects malformed colors, durations, duplicate IDs, dangling
  character references, excessive project length, and unknown fields.
- `store.py` uses SQLite transactions for optimistic concurrency. Files live
  under generated identifiers; user filenames do not become filesystem paths.
- `service.py` is the common operation boundary for AI clients and REST routes.
- `render.py` takes an immutable project snapshot, renders each shot with a
  consistent frame size/FPS/audio format, concatenates, mixes music, and writes
  MP4, SRT, and the snapshot. Cancellation is checked between and during stages.
- `mcp_server.py` registers real tools with read/write/destructive annotations.
- `auth.py` persists clients and hashed opaque tokens. MCP SDK handlers implement
  PKCE verification, OAuth request parsing, and redirect/client validation.
- `web.py` mounts the MCP application and explicitly runs its session manager's
  lifespan. This is necessary for Streamable HTTP to work inside FastAPI.
- `static/` presents an editable storyboard and a separate rendered-video view.

## Add a generative backend

Implement an adapter that accepts a `Scene`, character references, and a configured
model/workflow. Submit to a user-configured backend, track its real job, and import
the resulting media using `import_bytes`. Set the resulting `asset_id` through
the existing revision-checked editing service. This leaves composition unchanged.

A ComfyUI integration should expose capability only after checking the configured
workflow and models. Validate output media, avoid arbitrary remote file paths,
record model/workflow/seed provenance, and distinguish cancellation from failure.
Do not present a placeholder image or canned clip as generated output.

## Durable workers

The MVP runs one thread per service process. For scale, replace the executor with
a durable queue and leased jobs; retain immutable snapshots and explicit statuses.
The present queue records PIDs to detect stopped workers, not distributed leases.
PID reuse and abrupt power loss are reasons to move to leases before hosted scale.

## Security boundary

This is a single-owner self-hosted studio. An authorized AI can edit every project
and consume local CPU/disk by importing and rendering. It has no shell tool and
cannot fetch arbitrary remote URLs. Local-path imports require an explicit root
and are omitted from the remote tool set.

Default HTTP binding is loopback. Non-loopback CLI binding requires a public
HTTPS origin and owner key. Public deployments still need TLS termination, proxy
body/rate limits, resource quotas, patching, backups, and process isolation.
FFmpeg decodes untrusted media; keep it current and isolate deployments that accept
media from anyone other than the owner. This MVP has not had an external security audit.

Client secrets required for OAuth client authentication are stored in the local
SQLite file; its permissions are restricted. Access/refresh bearer tokens and
authorization codes are stored under SHA-256 hashes. Backups of the data directory
must be treated as private, including SQLite WAL files.

No telemetry, analytics scripts, external font requests, or remote AI calls are
made by the application. A user's connected AI platform still receives the
project/tool data shared with that platform.
