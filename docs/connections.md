# Connect an AI client

OpenReel supplies the same production and generation tools to its browser studio
and connected AI clients. The connected model plans scenes using its existing
session. OpenReel does not consume an LLM API key to direct a project.

There are two media generation routes:

- **Inside OpenReel:** select a configured ComfyUI workflow in **Generate media**.
  The server runs the workflow and imports its real output. Your AI can invoke
  this identical workflow through MCP.
- **Inside your AI client:** create an **AI handoff** request, then ask a connected
  client to generate the image or clip using its own available tools. The client
  imports the output and completes the saved request. You can also upload its
  output in the studio. A handoff does not open a chat, trigger a client remotely,
  or imply that the client has media generation tools.

See [generation setup](generation.md) for model/workflow configuration. A platform
subscription is not an API credential and does not automatically grant image,
video, or file-transfer capabilities to an MCP integration. This is an independent
MCP integration, not an official ChatGPT, Claude, or Gemini marketplace listing.

## Codex and Claude Code: local HTTP

Start `uv run openreel studio` from the repository and keep it running.

```sh
codex mcp add openreel --url http://127.0.0.1:8787/mcp
codex mcp list

claude mcp add --transport http openreel http://127.0.0.1:8787/mcp
claude mcp list
```

Refresh the client session if tools are not listed immediately. Local HTTP and
the studio share the exact same service, assets, and render jobs.

## Local stdio

Use an absolute repository and data path. This avoids dependence on the AI
client's current working directory or executable search path.

```sh
codex mcp add openreel -- uv run --directory /absolute/path/to/openreel \
  openreel mcp --data-dir /absolute/path/to/openreel/.openreel

claude mcp add --transport stdio openreel -- \
  uv run --directory /absolute/path/to/openreel \
  openreel mcp --data-dir /absolute/path/to/openreel/.openreel
```

For an MCP client using JSON configuration:

```json
{
  "mcpServers": {
    "openreel": {
      "command": "uv",
      "args": [
        "run", "--directory", "/absolute/path/to/openreel",
        "openreel", "mcp", "--data-dir", "/absolute/path/to/openreel/.openreel"
      ]
    }
  }
}
```

Optionally append `--import-root /absolute/path/to/media` to allow `import_asset`
to copy files from that directory. Symlinks escaping the directory are refused.
Without this flag, use `upload_asset` or the studio. A remote OAuth server never
exposes the local-path import tool.

Official configuration references: [Codex MCP](https://developers.openai.com/codex/mcp)
and [Claude Code MCP](https://code.claude.com/docs/en/mcp).

## Gemini CLI

Gemini CLI supports the same Streamable HTTP MCP endpoint:

```sh
gemini mcp add --transport http openreel http://127.0.0.1:8787/mcp
gemini mcp list
```

Alternatively merge the following into `.gemini/settings.json` for your project:

```json
{
  "mcpServers": {
    "openreel": {"httpUrl": "http://127.0.0.1:8787/mcp"}
  }
}
```

Use `httpUrl`, not the SSE `url` setting, for this transport. Keep OpenReel running
and use `/mcp` in Gemini CLI to inspect tool availability. Refer to the
[official Gemini CLI MCP guide](https://geminicli.com/docs/tools/mcp-server/) for
remote OAuth and configuration options. This documents **Gemini CLI**, not an
unverified extension for the Gemini consumer web/mobile app.

Copyable configuration files and an agent production prompt are in
[`examples/integrations`](../examples/integrations). Merge settings with your
existing configuration rather than replacing it. Leave tool approvals under your
client's control.

## ChatGPT Web and Claude Web

The remote transport and OAuth flow are implemented. Actual account pairing
requires an endpoint reachable from the platform; this project does not create
a public deployment or change your AI account settings automatically.

1. Choose an HTTPS hostname, for example `https://reel.example.com`.
2. Generate a private owner key:

   ```sh
   python -c 'import secrets; print(secrets.token_urlsafe(48))'
   ```

3. Store the result in your environment and start the service:

   ```sh
   export OPENREEL_OWNER_KEY='paste-your-generated-key-here'
   uv run openreel studio --public-url https://reel.example.com
   ```

4. Route that HTTPS origin to `127.0.0.1:8787` with a TLS reverse proxy or tunnel
   you control. Preserve the public Host header. Forward the **whole origin**,
   including `/mcp`, `/authorize`, `/token`, `/register`, `/revoke`, `/oauth/consent`,
   and `/.well-known/*`. A proxy configuration forwarding only `/mcp` is insufficient.
   Add request-size and rate limits at the edge. Do not expose an unauthenticated
   local server through a tunnel.
5. In ChatGPT's custom MCP server/plugin setup, add
   `https://reel.example.com/mcp` and choose OAuth. In Claude's connector setup,
   add the same URL. Dynamic registration is supported, so no pre-created client
   ID or client secret is needed.
6. On OpenReel's consent screen, verify the client and return address, enter your
   owner key, and authorize. Access tokens last one hour; rotating refresh tokens
   last up to 30 days of inactivity. All authorized clients can access the
   single owner's complete studio.
7. Ask the client to call `get_capabilities`, then create and render a project.

The owner key is an OpenReel server credential. It is not your ChatGPT/Claude
password and should never be given to the model in a prompt.

Current platform setup and access requirements are documented in the
[official ChatGPT guide](https://developers.openai.com/api/docs/guides/custom-mcp-server)
and [official Claude guide](https://support.claude.com/en/articles/11175166-get-started-with-custom-connectors-using-remote-mcp).
Check them for account/workspace restrictions and secure-tunnel options. Existing
AI access does not remove a platform's usage limits or supply a video-generation GPU.

### Remote exports

MP4/SRT download endpoints are authenticated. The MCP tool returns their URLs,
but a plain link in a chat is not a public share link. Open the remote studio,
enter your owner key, select the project, and use **Latest export**, or retrieve
the URL with the current OAuth bearer token. The browser studio uses authenticated
fetches and blob URLs for its media. Never put bearer tokens in query strings.

### Revoking connections

Clients can call the standard `/revoke` endpoint. Revocation invalidates the
whole access/refresh family. To revoke every connection, stop the server and
remove authorization rows with the following local maintenance command, then
restart it. This removes client registrations too, so clients must reconnect:

```sh
python -c "import sqlite3; c=sqlite3.connect('.openreel/openreel.sqlite3'); c.execute('DELETE FROM auth'); c.commit(); c.close()"
```

Rotating the owner key alone does not revoke already-issued OAuth tokens.

## Protocol workflow

1. `get_capabilities`
2. `create_project(title, brief, aspect_ratio)`
3. Optionally `add_character` and `upload_asset` / local `import_asset`
4. `create_storyboard(project_id, scenes, expected_revision)`
5. `generate_asset(project_id, request)` with a configured provider, or inspect
   pending `list_generation_jobs(project_id)` requests created in the studio
6. Poll `get_generation_status(job_id)`. For `awaiting_agent`, generate real media
   using the client's tools, import it, and call
   `complete_generation(job_id, asset_id, provenance)`; otherwise poll the
   configured provider until it finishes. An imported result remains available
   even when a concurrent edit prevents attaching it to the scene.
7. `update_scene` / `update_project` for timing, ordering, audio, and captions
8. Optionally `generate_voice` using an installed Piper voice, then
   `transcribe_scene` for word captions with configured Whisper
9. `render_video(project_id, resolution)`
10. Poll `get_render_status(job_id)` until completed, failed, or canceled

Inspect each tool's published schema for its current arguments. Generation
statuses are `queued`, `generating`, `finalizing`, `awaiting_agent`, `completed`, `failed`, and
`cancelled`. Never report a handoff request as a generated asset. Neither the
studio nor MCP fabricates generated media when a provider is unavailable.

Edits require the latest revision. On conflict, call `get_project`, reconcile,
and retry. Do not blindly overwrite another editor's changes.

Raw API documentation is available at `/docs` in local mode. Mutating REST
requests need `X-OpenReel-Request: studio` or an Authorization header.
