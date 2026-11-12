# Verification

Verified on 12 November 2026 with Python 3.13 on Windows.

- **69 automated tests pass** (`uv run pytest -q`).
- Ruff lint and formatting checks pass.
- JavaScript syntax check passes (`node --check src/openreel/static/app.js`).
- Source distribution and wheel build successfully (`uv build`).
- Real FFmpeg tests encode and decode MP4s in all three aspect ratios, including
  audio mixing, imported video, Tamil captions, cancellation, and timed words.
- Queue tests cover concurrent claims, stale leases, restart recovery, immutable
  snapshots, cancellation, and scratch-allocation failures without orphan heartbeats.
- A remote worker roundtrip claims through HTTP, downloads image/audio inputs,
  renders a real MP4, returns it to the coordinator, and downloads the published
  artifact. Tests reject invalid credentials, stale leases, unrelated assets,
  canceled output, and invalid media.
- ComfyUI adapter tests use mocked HTTP responses with actual PNG/MP4 decoding.
  They cover prompt/seed/reference mapping, unavailable models, provider errors,
  output size limits, streamed import/cleanup, cancellation, lease ownership,
  and recovery without duplicate submission.
- Studio-to-MCP tests create a persistent agent request through REST, initialize
  MCP, upload real media, complete the handoff, and verify scene attachment.
  Reference images are returned as bounded MCP image content. Long-running MCP
  media calls are tested concurrently with studio health requests.
- Transcription tests exercise the optional model call contract, local-only
  weights, timestamp validation, excessive output, silence, and concurrent edits.
- Existing stdio/HTTP handshakes, OAuth PKCE, consent, code replay, refresh
  rotation, and revocation coverage continues to pass.

An isolated headless Edge browser checked the local studio at 1440 px and 390 px:
no horizontal overflow, unavailable ComfyUI controls disabled, agent requests
persisting across reload, actual image upload/completion, attachment and provenance,
word timing edits and overlap validation, cancellation, Gemini CLI help, and no
browser JavaScript errors. The QA image was explicitly labeled as a test fixture;
it was not represented as AI-generated art.

The test run emits two upstream Starlette/HTTPX deprecation warnings. They do not
affect these results.

Not verified here: inference using live ComfyUI/Whisper/Piper model weights,
ChatGPT/Claude/Gemini account pairing, remote workers across real external HTTPS
infrastructure, Docker execution, a two-hour/1,000-scene benchmark, or an independent
security audit. HTTP worker tests run in-process against the real application.
Whisper provides estimated word timestamps rather than a separately verified
forced-alignment model. The application remains single-owner; marketplace
publication, tenant isolation, and GPU-aware scheduling are not implemented.
