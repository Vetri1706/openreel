# Connect an AI client

OpenReel supplies tools. The connected model plans scenes and calls those tools
using its existing session. No LLM API key is consumed by OpenReel.

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

Official configuration references: [Codex MCP](https://learn.chatgpt.com/docs/extend/mcp?surface=cli)
and [Claude Code MCP](https://code.claude.com/docs/en/mcp).

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
5. `update_scene` / `update_project` for timing, ordering, audio, and captions
6. Optionally `generate_voice` using an installed Piper voice
7. `render_video(project_id, resolution)`
8. Poll `get_render_status(job_id)` until completed, failed, or canceled

Edits require the latest revision. On conflict, call `get_project`, reconcile,
and retry. Do not blindly overwrite another editor's changes.

Raw API documentation is available at `/docs` in local mode. Mutating REST
requests need `X-OpenReel-Request: studio` or an Authorization header.
