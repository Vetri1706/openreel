# Audio and multilingual captions

Import recordings in the studio or through MCP, then assign an audio asset ID to
`scene.audio_asset_id`. Narration starts at the scene boundary and is trimmed or
padded to the scene duration. Listen to the rendered MP4 before publishing.

Use `project.music_asset_id` for a backing track and `music_volume` from 0 to 1.
Music loops across the complete timeline. Imported video clips are treated as
visuals only; their original sound is not mixed automatically.

## Optional Piper

Piper is an external local speech tool. OpenReel does not bundle its code or voice
weights. Install it following the [maintainer's instructions](https://github.com/OHF-Voice/piper1-gpl),
and make the `piper` executable available on PATH. Download a compatible voice's
`.onnx` **and matching `.onnx.json`** file to a directory you control.

```sh
export OPENREEL_PIPER_MODELS='/absolute/path/to/voices'
uv run openreel doctor
uv run openreel studio
```

Set each scene's `narration`, then ask your connected AI to call `generate_voice`
with the project ID, scene ID, exact `.onnx` filename from `get_capabilities`, and
current revision. The result is stored as a normal WAV asset and attached to the
scene. A missing binary or model produces an actionable error, not fake audio.

The adapter invokes Piper's CLI; real synthesis requires installation and is not
part of the default test environment. Check the voice's supported language and
license. OpenReel does not assume a Tamil voice is installed. You can always
import a Tamil recording you already have.

## Captions and fonts

Scene subtitles are drawn with Pillow and burned into the exported video. SRT
captions use the same scene boundaries, rounded to frame durations. Latin text
uses DM Sans; strings containing Tamil code points use Noto Sans Tamil. Font
shaping depends on the Pillow build; the standard installed wheel includes the
required layout engine on the tested platform. For another writing system or a
mixed-script font, set `OPENREEL_FONT` to a suitable font file before starting.

These are scene captions, not word-aligned speech transcription. Text wraps and
shrinks to fit. Very long captions should be split across multiple scenes.
