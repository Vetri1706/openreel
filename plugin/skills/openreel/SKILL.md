---
name: openreel
description: Direct, generate assets for, edit, and export films in OpenReel using the connected agent's media tools or code-rendered scenes. Use for OpenReel production requests and pending studio generation handoffs.
---

# OpenReel production

You direct and create; OpenReel stores the project, coordinates assets, previews
the timeline, and renders exports. Use the user's existing AI session and available
tools. Installing this skill does not install an image model or grant access to a
client's private image/video generator.

## Connect and choose the production route

Find the connected OpenReel MCP tools by their published names and schemas;
client prefixes can differ. Call `get_capabilities` and read the intended project.
Use a new project for a new film unless the user asks to continue an existing one.
If no server is connected, use the repository's [connection guide](https://github.com/Vetri1706/openreel/blob/main/docs/connections.md).
The plugin connects to a running studio at `http://127.0.0.1:8787/mcp` by default.
Browser-hosted clients need the documented authenticated HTTPS endpoint.

Keep the user's requested visual style and media method:

- **Native agent media:** use the image/video tools actually available in your
  current client. OpenReel's `agent` provider tracks and imports the result; it
  does not call the client's private generator. This route needs no separate
  generation API key in OpenReel.
- **Code-rendered visuals:** for a requested animation/illustration style that
  suits code, write original scene artwork and motion in the agent workspace,
  render actual media with available local tools, then import it. Read
  [procedural scenes](references/procedural-scenes.md) when taking this route.
- **Configured ComfyUI:** use `generate_asset` with a workflow returned by
  `get_capabilities` when the user chooses that backend. Configuration is not a
  live-readiness guarantee; inspect the actual job result.

If the needed generator or runtime is unavailable, explain the specific missing
capability. Do not replace requested photorealistic art with geometric drawings,
title cards, demo illustrations, or unrelated stock media without the user's
agreement. An uploaded user asset is also a valid production input.

## Fulfill a studio handoff

1. Call `list_generation_jobs` and `get_generation_status` for the requested job.
   Continue an existing request rather than creating duplicate generation work.
2. For `awaiting_agent`, read the prompt, media kind, requested seed, and reference
   asset. `read_image_asset` returns a real thumbnail for visual inspection. A
   requested seed is a request, not proof that a native tool honored it.
3. Generate the actual asset using your available tool, or render the requested
   original code artwork. Inspect its output before importing it.
4. Use `import_asset` only within the server's configured import root. Otherwise
   `upload_asset` accepts at most 8 MiB of decoded media. For larger files use the
   studio's file upload or an already authorized local import root. Do not paste
   large base64 files into a user-facing message. If the client cannot transfer
   its result, give the user the file and leave the request pending for upload.
5. Call `complete_generation(job_id, asset_id, provenance)` with accurate
   `producer`, optional `model`, and optional `notes`. For procedural art, record
   the renderer and source location in `notes`; do not label it as model-generated.
6. Verify the returned status and attachment. A revision conflict leaves the
   generated asset in the library. Read the current project and reconcile before
   attaching it. Unrelated edits can be preserved while applying the intended
   result with the latest revision. If the target scene was deleted or its visual
   direction/selection changed, ask which result to use when the user's intent
   is unclear; do not overwrite that choice blindly.

When starting native generation from the agent rather than an existing studio
request, first create a `generate_asset` request with `provider="agent"`, its
prompt/kind, and, for automatic scene attachment, `scene_id` plus current
`expected_revision`. Then follow the same completion flow.

## Produce and verify the film

Build a coherent storyboard for the user's brief. A concise visual plan, reusable
character references, and shared timing help keep independently generated scenes
consistent. Preserve existing scene IDs on edits and use current revisions.

Source-video audio is ignored by OpenReel: import a separate narration/soundtrack
asset and attach it explicitly. Narration text alone is silent. Use an installed
Piper voice or the user's available audio tools when requested; do not claim a
voice exists until a real recording is attached. `transcribe_scene` needs the
configured optional local Whisper model. Review its words and timing.

Call `render_video`, then poll `get_render_status` until it completes, fails, or
is canceled. Inspect representative frames and listen to audio when your tools
allow it. Only claim or link artifacts that actually exist; clearly report
pending transfer or export steps and any unverified part. Remote download URLs
require authentication; never put bearer tokens in
links. Include editable source files for original code-rendered scenes.
