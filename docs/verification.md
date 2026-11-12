# Initial build verification

Verified on 12 November 2026 with Python 3.12 on macOS ARM64.

- 15 automated tests pass (`pytest -q`).
- Ruff lint and format checks pass.
- JavaScript syntax check passes (`node --check src/openreel/static/app.js`).
- Python source distribution and wheel build successfully.
- The browser studio was checked at a narrow 608 px viewport: scene editing,
  saving, restored edits, export progress, completed export, and MP4 player.
- A real 16-second, four-scene sample was rendered at 1280 × 720, 24 fps.
- Integration tests encode and decode MP4s at all three aspect ratios, including
  audio mixing, imported video clips, Tamil captions, and cancellation.
- Both stdio and Streamable HTTP handshakes/tool calls pass. Stdio testing creates
  a project, saves a storyboard, exports a video, and polls until completion.
- OAuth testing covers registration, redirect validation, owner consent, PKCE,
  code replay, bearer-authenticated tools, refresh rotation, and family revocation.

The test run emits a Starlette deprecation warning for its current HTTPX-based
test client; it does not affect the application or test result.

Not verified in this build: live ChatGPT/Claude account pairing, externally hosted
HTTPS, Piper synthesis with actual voice weights, Docker execution, or an
independent security audit. ComfyUI, generative video models, and Whisper are
future integrations and are not represented as available capabilities.
