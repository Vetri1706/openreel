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

## Generation and native-agent handoff

`generation.py` persists generation jobs separately from render jobs. Studio REST
and MCP use the same manager. ComfyUI jobs snapshot an owner-configured API graph,
bind prompt/seed/reference inputs, validate node/model availability, submit a real
prompt, poll its history, and validate imported output. Agent handoffs remain
`awaiting_agent` until a connected client or user supplies real media. Provider,
model/workflow, prompt, and seed provenance accompanies the result.

Capability discovery distinguishes configured adapters from verified live readiness.
Clients cannot supply backend URLs or executable workflow graphs. Asset attachment
uses optimistic revisions; a conflicting edit keeps the new asset in the library.
No placeholder image or canned clip represents a successful generated result.
See [generation](generation.md) for lease recovery and cancellation semantics.

`captions.py` runs optional local Whisper inference in a bounded subprocess.
Word timings are scene-relative, ordered, non-overlapping, and used by preview,
FFmpeg overlays, and SRT export. See [captions](captions.md).

## Durable workers

SQLite stores an immutable project snapshot for every export. Local and remote
workers atomically claim renewable leases. Only the current lease may update a
job; expired workers cannot publish results. Each attempt writes to its own output
directory. Restart recovery re-encodes from the snapshot. Existing completed
exports remain downloadable; legacy pending exports lacking snapshots fail clearly.

`remote.py` lets trusted workers claim, download snapshot assets, heartbeat, and
stream their result to the coordinator using a dedicated worker key. SQLite stays
on the coordinator. Server-generated SRT/snapshot files accompany validated video.
Worker endpoints reject owner/OAuth credentials unless they match the separate
worker key. See [workers](workers.md) for deployment and configurable caps.

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

No telemetry, analytics scripts, or external font requests are made. Configured
ComfyUI generation sends prompts and selected reference media to that backend;
remote workers receive render snapshots and referenced assets. A connected AI
platform receives the project/tool data shared with it. API secrets stay on the
server; native agent tools remain controlled by their own client.
