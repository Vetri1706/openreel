# Connect the same studio to your AI

Start `uv run openreel studio`, then use the instructions in
[`docs/connections.md`](../../docs/connections.md).

| Client | Example | Destination |
| --- | --- | --- |
| Codex | `codex.config.toml` | Merge into your Codex `config.toml`, or use `codex mcp add` |
| Claude Code | `claude.mcp.json` | Merge into project `.mcp.json`, or use `claude mcp add` |
| Gemini CLI | `gemini.settings.json` | Merge into project `.gemini/settings.json`, or use `gemini mcp add` |
| ChatGPT / Claude web | Remote MCP + OAuth | Follow the HTTPS setup in the connection guide |

These are configuration examples, not installed or published marketplace
plugins. They connect local clients to `http://127.0.0.1:8787/mcp`. Web clients
need your configured reachable HTTPS origin. Do not expose the local endpoint
without the documented authentication setup.

The [director prompt](director-prompt.md) describes the shared media-generation
handoff and production workflow. Paste it into your connected AI, or use
OpenReel's `direct_video` MCP prompt.
