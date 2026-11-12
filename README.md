# OpenReel

**Your AI directs. Your machine renders.**

An Apache-2.0 video production engine with an MCP server and a local browser studio.
Generate images and clips inside the studio through configured ComfyUI workflows,
or let a connected AI agent generate assets with its own media tools and return
them to the same project. Connect Codex, Claude Code, Gemini CLI, or an MCP-capable
web client to direct, edit, caption, and export. Local composition needs no LLM
API key or paid video service.

## Run it

Requires Python 3.11+ and [uv](https://docs.astral.sh/uv/). From this repository:

```sh
uv sync --extra dev
uv run openreel studio
```

Open **http://127.0.0.1:8787**. Choose **Open demo film** for a working four-scene
project, or start from scratch. Export produces a real H.264/AAC MP4 plus SRT
subtitles and the exact project snapshot used for the render.

Alternatively:

```sh
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -e '.[dev]'
openreel studio
```

FFmpeg is supplied by `imageio-ffmpeg` on its supported platforms; an installed
system FFmpeg is preferred. Run `uv run openreel doctor` to inspect your setup.
The initial dependency installation needs internet; composition and rendering do not.

## Connect your director

With the studio running:

```sh
# Codex
codex mcp add openreel --url http://127.0.0.1:8787/mcp

# Claude Code
claude mcp add --transport http openreel http://127.0.0.1:8787/mcp
```

Then ask your connected client:

> Use OpenReel to make a 20-second film about slowing down. Write four scenes
> with title cards, captions, and slow camera movement. Render a 720p MP4 and
> wait for the export to finish. Do not claim to generate images or voices
> unless the corresponding media is actually available.

The director can import your images or clips, attach recordings, keep character
references, change the timeline, and regenerate an export. The studio checks for
external edits every five seconds while you are not editing or playing.

For stdio setup, ChatGPT Web, Claude Web, HTTPS, OAuth, and remote downloads, see
[Connections](docs/connections.md). Browser clients require an accessible endpoint
or supported tunnel. Their account permissions and usage limits still apply.

## Generate in the app or with your agent

Select a scene and open the generation controls. Choose **ComfyUI** for an installed
image or video workflow, or **Connected AI agent** to create a persistent request.
In your connected AI client, ask it to fulfill OpenReel's pending generation
requests using its available media tools. Both paths import actual output into the
media library and attach it only if the project revision still matches.

The MCP integration exposes `generate_asset`, `list_generation_jobs`,
`get_generation_status`, `complete_generation`, and `cancel_generation` alongside
the editing and export tools. It does not gain access to a client's private native
image generator automatically. If that client cannot transfer generated files,
download the result and import it through the studio's generation job.

See [generation setup](docs/generation.md) for ComfyUI workflow bindings and
[connections](docs/connections.md) for ChatGPT, Claude, Codex, and Gemini setup.
Model weights and optional ComfyUI nodes are installed separately; placeholder
art is never presented as an AI result.

## What works

| Area | Implemented |
| --- | --- |
| MCP | stdio + Streamable HTTP, typed tools, structured results, director prompt |
| Projects | SQLite persistence, optimistic revision checks, editable scene ordering |
| Media | PNG/JPEG/WebP, MP4/MOV/WebM, WAV/MP3/M4A/OGG/FLAC imports |
| Generation | ComfyUI image/video workflows; persistent native-agent handoff; validated outputs and provenance |
| Composition | Still images, slow push/pull/pan, looped clips, cuts, fades, title cards |
| Audio | Imported voiceovers, background music mixing, optional installed Piper voices |
| Captions | Scene captions or timed word captions, optional local Whisper transcription, SRT export, Latin and Tamil fonts |
| Export | 16:9, 9:16, 1:1; 360p draft, 720p, 1080p; 24 or 30 fps; no watermark |
| Jobs | Durable leased queue, restart recovery, local and authenticated HTTP workers, cancellation, immutable exports |
| Characters | Reference images, descriptions, seeds, scene assignments |
| Remote access | Single-owner OAuth with PKCE, explicit consent, rotating refresh tokens |
| Studio | Responsive editor, media library, timeline, preview, final MP4 playback |

## Scope and practical limits

OpenReel coordinates generation with real configured models or an agent's available
media tools. It does not include model weights, bundle a ChatGPT/Claude/Gemini
subscription, or impersonate those services. The studio's visual prompts submit
generation jobs; storyboard authorship remains with you or your AI client.

- The storyboard preview approximates composition and timing. The rendered MP4
  is authoritative for fonts, framing, audio, and fades.
- Fades are short fades through black at each scene boundary, not overlapping dissolves.
- Source-video audio is ignored; attach a voiceover separately. Clips and music
  loop; scene voiceovers are padded or trimmed to the scene duration.
- Narration text alone produces no sound. [Piper setup](docs/audio.md) is optional.
- Word transcription requires the optional `captions` extra and a local faster-whisper model. You can also edit word timestamps directly; see [captions](docs/captions.md).
- Character references are production notes; they do not enforce visual identity.
- Defaults: 1,000 scenes, two hours per project, ten minutes per scene, and 500 MiB per imported file. All are owner-configurable. MCP base64 uploads retain an 8 MiB transport bound; larger files use streaming studio uploads or allowed local imports.
- The queue accepts 32 active/queued exports by default. Each worker renders one job at a time. Interrupted exports retry from saved snapshots after their leases expire; encoding restarts rather than resuming a partially rendered scene.
- Use `openreel worker` locally or `openreel worker --server https://your-studio.example` on another machine. See [workers and limits](docs/workers.md). GPU scheduling and multi-tenant quotas are not implemented.
- The data directory is single-owner, not a multi-tenant hosting service.
- Adapter tests use controlled providers. Live ComfyUI inference, Whisper weights, AI account pairing, external HTTPS deployment, Docker, and Piper synthesis need their respective environments; see [verification](docs/verification.md).

## Architecture

```text
ChatGPT / Claude / Codex / Gemini CLI
                  │ MCP
                  ▼
     typed tools + director instructions
                  │
         shared project service ◀──── browser studio / REST
                  │
       SQLite metadata + local assets
                  │ immutable snapshot
                  ▼
       FFmpeg render worker + Pillow captions
                  │
            MP4 + SRT + project.json
```

```text
src/openreel/
  mcp_server.py   MCP tools and production guidance
  service.py     Shared editing, imports, optional Piper integration
  generation.py  ComfyUI jobs and persistent native-agent media handoffs
  captions.py    Optional local Whisper word transcription
  config.py      Shared owner-configured resource limits
  models.py      Validated projects, scenes, characters, assets
  store.py       Transactional persistence and authorization records
  render.py      Cancelable FFmpeg composition pipeline
  remote.py      Authenticated HTTP render worker transport
  media.py       Media validation, fonts, overlays, original demo art
  auth.py        Single-owner OAuth provider for SDK protocol handlers
  web.py         REST API, authentication, studio, MCP mounting
  static/        Browser editor and self-hosted OFL fonts
tests/           Real rendering, protocol, revision, and auth tests
docs/            Setup, architecture, security, and contributor notes
```

## Development

```sh
uv run pytest -q
uv run ruff check src tests
uv run ruff format --check src tests
```

Tests include actual MP4 encoding and decoding, all three aspect ratios, Tamil
captions, image/video/audio imports, music mixing, cancellation, MCP handshakes
over both transports, PKCE, consent, authorization replay, and token revocation.

Project data defaults to `.openreel/` in your working directory. Set
`OPENREEL_DATA_DIR` or `--data-dir` to an absolute path when multiple clients need
to share the same studio. Back up the complete directory with the server stopped.

## Further work

Richer transitions, clip trimming, GPU-aware scheduling, tenant isolation and
quotas, and published client marketplace listings remain future work. The current
client integration is the MCP server, with copyable configuration examples in
[`examples/integrations/`](examples/integrations/). See [architecture](docs/architecture.md).

## License

OpenReel source and original demo illustrations: [Apache-2.0](LICENSE).
Bundled fonts retain their SIL Open Font License files. FFmpeg, codecs, Piper,
and voice/model weights have their own licenses; see [NOTICE](NOTICE).

This is an independent project, not affiliated with OpenAI, Anthropic, or Google.
