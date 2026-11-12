# Render workers and resource limits

OpenReel persists every export's exact project snapshot before returning its job
ID. A worker atomically claims the oldest eligible job and renews a 90-second
lease while encoding. A killed worker's job becomes eligible for another worker
after its lease expires; the replacement starts that export again from its
snapshot. Scene edits made after submission do not change the export. Partial
outputs are never published as completed videos.

The studio runs one embedded CPU render worker by default. Set
`OPENREEL_RENDER_WORKER=0` to run an API-only studio, then start one or more workers:

```sh
openreel worker --data-dir /absolute/path/to/.openreel
```

Local workers must share the same data directory on the same machine. SQLite WAL
is not a distributed database: do not place its database on a network share.
Workers claim different jobs; an individual film is rendered by one worker. Each
worker uses a bounded FFmpeg encoder thread count. This is CPU rendering, not a
GPU scheduler.

## Workers on another machine

Set a dedicated `OPENREEL_WORKER_KEY` on the studio and worker. It must contain at
least 32 random characters, and should differ from `OPENREEL_OWNER_KEY`. Serve the
studio over HTTPS using the existing authenticated remote setup. On another
machine with OpenReel and FFmpeg installed:

```sh
export OPENREEL_WORKER_KEY='your-dedicated-random-worker-key'
openreel worker --server https://reel.example.com --data-dir /absolute/path/to/worker-cache
```

In PowerShell set environment variables with `$env:OPENREEL_WORKER_KEY = '...'`.
Use environment variables to avoid exposing credentials in process arguments.
The worker claims a snapshot over HTTP, downloads only assets referenced by that
job, renders in a temporary local directory, and uploads the actual MP4. The
server validates the lease and media before making the MP4, SRT, and snapshot
available. Heartbeats continue during downloads, encoding, and upload. Pending
exports remain in SQLite when no worker is connected. Worker credentials allow
processing jobs and accessing their input media, so give them only to trusted
machines. They do not authenticate ordinary project or studio APIs.

Cancellation is recorded centrally and checked during encoding. Lost leases and
canceled jobs cannot be completed by an old worker. Each attempt writes to a
separate output directory, so an old attempt cannot overwrite another worker's
successful result. Temporary partial files from a hard process or machine crash
may need manual cleanup under `scratch/`; do not delete active workers' files.

## Configurable limits

Defaults now support larger single-owner projects. Configure these environment
variables consistently on the studio and workers before starting them. The
capabilities endpoint reports the effective values.

| Variable | Default | Allowed range |
| --- | ---: | ---: |
| `OPENREEL_MAX_DURATION_SECONDS` | 7200 (2 hours) | 1–86400 |
| `OPENREEL_MAX_SCENES` | 1000 | 1–10000 |
| `OPENREEL_MAX_SCENE_SECONDS` | 600 | 1–86400 |
| `OPENREEL_MAX_UPLOAD_MB` | 500 MiB | 1–10240 |
| `OPENREEL_MAX_QUEUED_RENDERS` | 32 | 1–10000 |
| `OPENREEL_RENDER_STAGE_TIMEOUT_SECONDS` | 3600 | 1–86400 |

The queue limit counts queued and active jobs together. Uploads use a bounded
stream to disk; audio and video files are not loaded completely into memory.
Images are decoded with a pixel limit and normalized to at most 4096×4096. MCP
base64 transfer remains capped at 8 MiB to avoid oversized model-context payloads;
use the studio's streaming upload for larger media.

Increasing a validation cap does not guarantee enough RAM, disk space, processing
speed, proxy upload limits, or free GPU memory in a separate generation backend.
These defaults are accepted limits, not a claim that a two-hour, 1000-scene film
has been benchmarked. Lowering limits below an existing project's size prevents
that project from loading until those limits are raised again.

The data directory, authorization model, and project library remain single-owner.
Remote workers add compute distribution, not tenant isolation or public SaaS
hosting. Keep TLS, backups, disk quotas, and process isolation appropriate for
your deployment.
