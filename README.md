# OpenReel

**Your AI directs. Your machine renders.**

An Apache-2.0 video production engine with an MCP server and a local browser studio.
Connect Codex, Claude Code, or an MCP-capable web client. Describe a film in that
client; it writes the storyboard and calls OpenReel's production tools. No separate
LLM API key, browser-session scraping, or paid video service is required.

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

## What works in 0.1

| Area | Implemented |
| --- | --- |
| MCP | stdio + Streamable HTTP, typed tools, structured results, director prompt |
| Projects | SQLite persistence, optimistic revision checks, editable scene ordering |
| Media | PNG/JPEG/WebP, MP4/MOV/WebM, WAV/MP3/M4A/OGG/FLAC imports |
| Composition | Still images, slow push/pull/pan, looped clips, cuts, fades, title cards |
| Audio | Imported voiceovers, background music mixing, optional installed Piper voices |
| Captions | Scene-level burned captions, SRT export, bundled Latin and Tamil fonts |
| Export | 16:9, 9:16, 1:1; 360p draft, 720p, 1080p; 24 or 30 fps; no watermark |
| Jobs | Background rendering, progress, cancellation, snapshot export history |
| Characters | Reference images, descriptions, seeds, scene assignments |
| Remote access | Single-owner OAuth with PKCE, explicit consent, rotating refresh tokens |
| Studio | Responsive editor, media library, timeline, preview, final MP4 playback |

## Scope and practical limits

This release makes **edited videos from images, clips, text, and audio**. It does
not generate photorealistic motion, images, or character reference images. It does
not call OpenAI or Anthropic APIs: the AI client supplies the intelligence. A
standalone prompt box is deliberately not presented as an independent AI service.

- The storyboard preview approximates composition and timing. The rendered MP4
  is authoritative for fonts, framing, audio, and fades.
- Fades are short fades through black at each scene boundary, not overlapping dissolves.
- Source-video audio is ignored; attach a voiceover separately. Clips and music
  loop; scene voiceovers are padded or trimmed to the scene duration.
- Narration text alone produces no sound. [Piper setup](docs/audio.md) is optional.
- Captions cover a complete scene. Word-level transcription and Whisper are not included.
- Character references are production notes; they do not enforce visual identity.
- Up to 60 scenes, 10 minutes per project, 60 seconds per scene, and 50 MB per
  imported file. MCP base64 uploads have an 8 MB limit.
- Run one HTTP worker. Up to four active/queued exports are accepted across
  processes sharing a data directory; each process renders one at a time.
- Keep the process running until exports finish. A killed worker's job is marked
  failed when next read. Jobs are not automatically resumed after restart.
- The data directory is single-owner, not a multi-tenant hosting service.
- The core, transports, render pipeline, and OAuth flow are covered by automated
  tests. Live ChatGPT/Claude account pairing, external HTTPS deployment, Docker,
  and Piper model synthesis require their respective environments and have not
  been end-to-end verified in this repository's initial build.

## Architecture

```text
ChatGPT / Claude / Codex / Claude Code
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
  models.py      Validated projects, scenes, characters, assets
  store.py       Transactional persistence and authorization records
  render.py      Cancelable FFmpeg composition pipeline
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

## Next milestones

1. ComfyUI image-to-video adapter with explicit workflow and model configuration.
2. Whisper alignment, word-level captions, richer transitions, and clip trimming.
3. Durable worker queue, GPU scheduling, remote render workers, and larger projects.

Those integrations are roadmap items, not simulated capabilities. See
[architecture](docs/architecture.md) for extension points.

## License

OpenReel source and original demo illustrations: [Apache-2.0](LICENSE).
Bundled fonts retain their SIL Open Font License files. FFmpeg, codecs, Piper,
and voice/model weights have their own licenses; see [NOTICE](NOTICE).

This is an independent project, not affiliated with OpenAI or Anthropic.
