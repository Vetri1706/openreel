# Word-timed captions

Install the optional runtime with `uv sync --extra dev --extra captions`. Download
a faster-whisper / CTranslate2 model using your preferred model-management tools,
then set `OPENREEL_WHISPER_MODEL` to its local directory before starting OpenReel.
The application uses `local_files_only=True`; inference never downloads weights.

Attach narration audio to a scene and choose the transcription control in the
studio, or call MCP `transcribe_scene` with `project_id`, `scene_id`, and the latest
`expected_revision`. Optional `language` is a two- or three-letter code; omit it for
detection. Narration text is not used as a substitute for real audio.

OpenReel trims the attached audio to the scene duration, requests Whisper word
timestamps, validates timings, and saves `caption_words` with `text`, `start`, and
`end` in seconds relative to the scene. Words appear in the preview, burned into
the MP4, and in SRT exports. When no timed words exist, the scene subtitle is used.
Clear the word list to return to scene-level captions.

CPU/int8 is the default. `OPENREEL_WHISPER_DEVICE` and
`OPENREEL_WHISPER_COMPUTE_TYPE` can select a compatible installed GPU runtime.
One transcription per service process runs at a time, with a ten-minute inference
timeout. Silence, errors, and concurrent edits preserve existing captions.
Model inference and hardware compatibility require installed weights and runtime;
protocol/validation tests do not establish transcription accuracy.

This uses Whisper's estimated word timestamps, not a separate forced-alignment
model such as WhisperX. Review and edit timings before publishing.

Reference: [faster-whisper word timestamps](https://github.com/SYSTRAN/faster-whisper#word-level-timestamps).
