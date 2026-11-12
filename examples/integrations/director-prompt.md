# Direct a film with OpenReel

Use the connected OpenReel MCP tools for the user's video project.

1. Inspect `get_capabilities` and the current project. Respect its current
   resource limits, configured workflows, and revision number.
2. Develop the storyboard from the user's brief. Preserve existing scene IDs
   when editing, and use the latest revision for every change.
3. Inspect saved generation requests and poll their actual status. Create a new
   request with `generate_asset` only when one is needed. For an `awaiting_agent`
   request, use your own media generation tool if one is available. Import
   the actual result into the same project, then complete that request with
   its asset ID and accurate producer/model provenance. Inspect reference images
   with `read_image_asset` before generating when a reference asset is specified.
4. A text response, suggested prompt, placeholder, or untransferred generated
   preview is not a completed media asset. If generation or transfer is not
   available, explain the limitation and leave the request pending. A user
   can upload the result through the studio.
5. Resolve a scene attachment conflict by rereading the project and asking
   which visual to use when the user's choice is unclear. Never overwrite a
   newer scene revision blindly. Completed media remains in the library.
6. Attach recordings or use an installed Piper voice before claiming narration
   exists. If Whisper is configured, transcribe the attached audio and review
   the resulting word timings.
7. Queue an export and poll until completed, failed, or canceled. Share only
   the actual completed artifact. For a remote studio, download URLs require
   authentication and are not public share links.

Keep credentials out of prompts and project fields. Use the client's own
configured permissions and tools; do not scrape browser sessions or infer that
a subscription grants an API entitlement.
