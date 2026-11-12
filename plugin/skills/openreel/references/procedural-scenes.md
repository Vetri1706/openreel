# Scenes created by the agent in code

This is a production method for styles such as vector animation, diagrams,
hand-drawn characters, typography, or a small 3D scene. It is not a substitute for
photorealistic image generation. The agent writes and runs the scene with its
own available coding/rendering tools; OpenReel imports the resulting media.
The server deliberately does not expose an arbitrary-code execution tool.

Choose a rendering stack that is already available or appropriate to the task:
Canvas/SVG for 2D, WebGL/Three.js or Blender for 3D, or the user's existing film
toolkit. If the user explicitly asks for Lemo-Opuscar, use their installed skill
and its style guides; this plugin does not bundle its styles, code, or assets.

Keep source, media, and intermediate renders in the user's project workspace.
Use one timeline for camera motion, character actions, captions, and audio cues.
Make each frame a deterministic function of time with explicit random seeds so
a still preview and final render agree. Keep the requested aspect ratio, frame
rate, and scene duration consistent with the OpenReel project.

Render and inspect early frames with the actual artwork, then check motion at
the key action times. A correct file duration alone does not verify performance,
legibility, framing, or the quality of the scene. Produce the requested original
visuals rather than presenting a reused demo as the user's film.

Import each rendered scene clip and its separate audio. Assign clips to scenes
at their intended durations; OpenReel loops source clips and ignores embedded
audio. Use scene-relative word captions when needed, or captions already burned
into the source clip, without duplicating them. For a complete pre-rendered film,
split it into clips when it exceeds the configured per-scene limit.

Record provenance accurately: `producer` identifies the agent/workflow, `model`
can identify the authoring model when known, and `notes` identifies the code-based
renderer and source files. Preserve the editable sources alongside the final MP4.
