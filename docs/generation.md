# Generate media in OpenReel or through your connected agent

OpenReel exposes the same generation jobs to the browser studio and MCP clients.
There are two routes:

- **ComfyUI:** OpenReel submits a configured image or video workflow to your running
  ComfyUI server, follows its real prompt ID, validates the output, and imports it.
- **Connected AI agent:** OpenReel saves a generation request. An agent with native
  image/video tools reads the request, uses those tools, uploads the actual file,
  and completes the request. The request stays pending until real media arrives.

An MCP connection does not itself grant image generation to a client. ChatGPT,
Claude, Gemini, Codex, and other clients retain their own tool availability,
account limits, and approvals. OpenReel never borrows a browser login or presents
an agent handoff as an automatically executed model. There are no required OpenAI,
Anthropic, or Gemini API keys in this integration.

## Configure ComfyUI

Install and run ComfyUI separately with the models and custom nodes required by
**your** workflow. Export the workflow using **Export (API)**. A normal editor
workflow containing a `nodes` array is not the API prompt format.

Create a server-side JSON configuration containing named workflows. For example,
wrap your exported prompt graph as follows; replace the node IDs with those in
your graph and replace the illustrative `prompt` value with the entire graph:

```json
{
  "portrait": {
    "name": "Character portrait",
    "kind": "image",
    "model": "my-installed-checkpoint.safetensors",
    "prompt": {"REPLACE": "with the complete exported API prompt object"},
    "bindings": {"prompt": ["6", "text"], "seed": ["3", "seed"]},
    "output_node": "9",
    "output_key": "images"
  }
}
```

The configuration above illustrates the wrapper and deliberately is not a
runnable model/workflow. Keep every node from your real export. The wrapper fields
are:

| Field | Meaning |
| --- | --- |
| Workflow key | Stable ID selected in the app or `workflow_id` in MCP requests |
| `kind` | `image` or `video` |
| `prompt` | Complete API-format graph of node IDs to `class_type`/`inputs` objects |
| `bindings.prompt` | Node ID and input name that receive the user's prompt |
| `bindings.seed` | Optional node/input receiving the requested seed |
| `bindings.reference` | Optional LoadImage node/input receiving an uploaded reference |
| `output_node` | Exact output node to read; preview nodes are ignored |
| `output_key` | History collection: `images`, `videos`, or `gifs` |
| `model` | Owner-provided model label retained in provenance |

For an image-to-video workflow, set `kind` to `video`, map the reference image to
its LoadImage input, and select the node producing the encoded clip. For example,
a VideoHelperSuite output may use `output_key: "gifs"` while actually returning an
MP4 filename. The adapter requires an MP4, MOV, or WebM file; it does not mistake
an animated GIF or an image sequence for an encoded clip. The workflow must save
the file to the ComfyUI `output` directory.

Set these environment variables **before starting OpenReel**:

```powershell
$env:OPENREEL_COMFYUI_URL = "http://127.0.0.1:8188"
$env:OPENREEL_COMFYUI_WORKFLOWS = "C:\my-config\workflows.json"
uv run openreel studio
```

On POSIX shells, use `export NAME=value` for the same variables. For a protected
ComfyUI reverse proxy, optionally set `OPENREEL_COMFYUI_TOKEN`; it becomes a Bearer
header. Keep it in the server environment. The app and MCP clients cannot supply
provider URLs, raw workflow graphs, or credentials. Redirects and environment
HTTP proxies are disabled for provider requests.

Capability inspection reports whether workflows are configured, not whether a
GPU or model is currently ready. Before submitting a prompt, the worker checks
`/object_info` for the configured node classes and dropdown values, including
model filenames. ComfyUI also validates the graph when receiving `/prompt`.
Missing models/nodes fail the job visibly. OpenReel does not silently download
weights, install custom nodes, or replace failed output with placeholder art.

## Native agent handoff

1. Create a generation request with provider `agent`, a prompt and media kind.
   To attach automatically, also supply a scene ID and its current project revision.
2. The agent reads pending generation requests. For a reference image, call
   `read_image_asset` with its asset ID. This returns actual MCP image content as
   a JPEG thumbnail up to 1024 pixels and 1 MiB, so the client can inspect the
   reference without needing a separate authenticated download. The original
   imported asset is unchanged; audio/video assets are rejected by this tool.
3. The agent invokes its own native media generator. If unavailable, it should
   explain the limitation and leave or cancel the request.
4. Upload the actual media to the same OpenReel project using the existing asset
   upload/import tools.
5. Complete the generation request using the returned asset ID. Optional provenance
   accepts `producer`, `model`, and `notes`; it is labeled **agent-reported**.

The app can also complete a handoff by attaching an already imported result. A
prompt alone, a URL to unavailable media, or a fabricated asset ID cannot complete
a request. Character reference images can be generated as library assets and then
assigned to a character. Identity consistency still depends on the generator and
workflow; a seed or reference image is not an identity guarantee.

## Jobs, revisions, and cancellation

Jobs persist in SQLite with the prompt, requested seed, reference asset ID,
provider prompt ID, workflow digest, resulting asset ID, and attachment outcome.
Agent requests survive restart. ComfyUI jobs use leases across app and stdio
processes sharing the same data directory. Polling or startup recovers an expired
lease and resumes following a known provider prompt ID. If a process died during
submission before saving its provider ID, the job fails rather than submitting
potentially duplicate work. Check ComfyUI before retrying that request.

Generated output is attached only if the scene still exists and the project
revision matches the request. Concurrent editing preserves your edit: the job
completes with `attached: false`, explains the conflict, and keeps the generated
asset in the media library for review. Finalizing an attachment is a short commit
phase and is no longer cancellable.

Cancelling a queued/running request prevents OpenReel from attaching the output.
Its worker attempts to remove only its own prompt from ComfyUI's pending queue.
An already running workflow may continue consuming GPU time. OpenReel deliberately
does not call the global `/interrupt` endpoint because it could stop another user's
workflow. To stop that computation immediately, use ComfyUI's own controls.

The service accepts at most 32 pending generation requests, has two generation
worker threads per process, limits workflow configuration to 2 MB and API responses
to 8 MB, enforces the configured imported-media size limit on output downloads,
and times out provider polling after one hour. Jobs are durable; ComfyUI's own
history is a separate dependency and may disappear when its server restarts.

## Verification

Automated tests exercise actual image decoding and video decoding with mocked
ComfyUI HTTP responses: prompt/seed mapping, reference upload, real output import,
provenance, missing-model and execution failures, unsafe output paths, oversized
responses, cancellation isolation, restart recovery without duplicate submission,
agent completion, project scoping, revision conflict, and competing worker leases.
A live ComfyUI installation with your particular model/custom nodes still needs
an end-to-end generation test.

Protocol references: [ComfyUI server routes](https://docs.comfy.org/development/comfyui-server/comms_routes),
[official API workflow example](https://github.com/Comfy-Org/ComfyUI/blob/master/script_examples/basic_api_example.py),
and [official output/history example](https://github.com/Comfy-Org/ComfyUI/blob/master/script_examples/websockets_api_example.py).
