# Install the OpenReel agent workflow

The package combines production instructions with the existing OpenReel MCP
server. Your agent plans the film, creates media with its available tools or code,
imports the real files, edits the project, and waits for a verified export. The
studio and agent use the same projects and generation requests.

The packaged MCP connection targets `http://127.0.0.1:8787/mcp`. Start the studio
on the same computer and keep it running:

```sh
uv sync --extra dev
uv run openreel studio
```

The plugin does not start another studio or install model weights. For another
host or port, change the connection in your own copy or use the manual
[connection instructions](connections.md). Keep secrets out of committed manifests.

## Use the branch before it is merged

These files were introduced on `codex/integrated-media-generation`. Until that
branch is merged, a marketplace installation from the repository's default branch
may not include them. Start from a checkout that contains `plugin/`:

```sh
git clone --branch codex/integrated-media-generation --single-branch https://github.com/Vetri1706/openreel.git
cd openreel
```

For an existing checkout, fetch and switch branches only after preserving your
own work. After the change is merged, a normal default-branch clone contains the
same package.

## Claude Code

For a temporary local test from the repository root:

```sh
claude --plugin-dir ./plugin
```

For a persistent installation from this local checkout:

```sh
claude plugin marketplace add .
claude plugin install openreel@openreel-tools
```

Restart the Claude Code session if needed, inspect the OpenReel server with
`/mcp`, and invoke `/openreel:openreel` with your film brief. The package contains
one skill under `skills/openreel/`; `.mcp.json` connects the running studio.

After the package reaches the default branch, it can also be installed directly:

```sh
claude plugin marketplace add Vetri1706/openreel
claude plugin install openreel@openreel-tools
```

The marketplace is maintained by this repository's owner. It is not an Anthropic
curated-directory listing. Validate a local package with
`claude plugin validate ./plugin`. See the official
[plugin manifest reference](https://code.claude.com/docs/en/plugins-reference)
for the layout and validation rules.

## Gemini CLI

From the checkout containing the package:

```sh
gemini extensions install ./plugin
```

Restart Gemini CLI, inspect `/mcp`, and ask it to use the OpenReel skill. The
extension declares the Streamable HTTP connection using `httpUrl`; its `skills/`
directory supplies the same production instructions. No separate `GEMINI.md` is
needed. Local-path installs and packaged skills are documented in the
[Gemini extension reference](https://geminicli.com/docs/extensions/reference/).

This package targets **Gemini CLI**. It does not install an extension in the
Gemini consumer website or mobile app, and it does not grant a Gemini model
image/video generation tools.

## Codex

Copy the complete `plugin/skills/openreel/` directory into your target project's
`.agents/skills/openreel/` directory. Keep its supporting files together. Then
connect the already running studio:

```sh
codex mcp add openreel --url http://127.0.0.1:8787/mcp
```

Start a new session and invoke `$openreel` with your brief. The skill is portable
instructions; the MCP connection supplies the actual production tools. See
[Codex skills](https://developers.openai.com/codex/skills) for discovery and scope.

## ChatGPT and Claude on the web

The local CLI manifests cannot make your machine's loopback address reachable
from a hosted AI service. Use OpenReel's authenticated remote MCP connection,
HTTPS deployment or supported tunnel, and the platform's own connector setup
from [Connections](connections.md#chatgpt-web-and-claude-web).

Web clients receive OpenReel's MCP tools and director instructions. Installing a
Claude Code or Gemini CLI package is not evidence of a ChatGPT or Claude web
marketplace installation. Public directory submission and live account pairing
remain separate work.

## Media creation and capability checks

The workflow supports the media-making tools the active agent actually has:

- A coding agent can write original artwork or animation code in its own
  workspace, execute it through its own tools, and import the resulting files.
- An agent with native image/video tools can fulfill an `awaiting_agent` request,
  inspect a reference with `read_image_asset`, import actual output, and call
  `complete_generation` with accurate provenance.
- A configured ComfyUI image/video workflow can be called from either the agent
  or the studio. Check `get_capabilities` first.

A text-only client cannot produce media merely by installing this skill. If a
required tool is missing, it must report that limit and use an available route
approved by the user's brief. This package does not scrape account sessions,
read another client's private generation records, or invent model access.
OpenReel's MCP server exposes typed production operations, not arbitrary code
execution; any authored code runs through the coding agent's own environment.

## Package contents and verification

- `.claude-plugin/marketplace.json`: the repository marketplace, pointing to `./plugin`.
- `plugin/.claude-plugin/plugin.json`: Claude Code identity and metadata.
- `plugin/.mcp.json`: Claude Code's connection to the running studio.
- `plugin/gemini-extension.json`: Gemini CLI identity and connection.
- `plugin/skills/openreel/SKILL.md`: shared agent production workflow.

JSON structure and local paths can be checked without installing anything in an
account. A successful manifest check does not establish live Claude/Gemini account
pairing or native media access. Those require the corresponding installed client
and a user-authorized session.
