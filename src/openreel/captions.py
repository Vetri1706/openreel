"""Optional, local Whisper word timestamps with bounded inference subprocesses."""

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

from .media import ffmpeg

_inference = threading.Lock()


def capabilities() -> dict:
    model = os.environ.get("OPENREEL_WHISPER_MODEL", "")
    installed = importlib.util.find_spec("faster_whisper") is not None
    configured = bool(model and Path(model).is_dir())
    return {
        "available": installed and configured,
        "configured": configured,
        "model": Path(model).name if model else None,
        "description": "Local Whisper word timestamps from attached narration; model weights are not downloaded automatically.",
        "reason": None
        if installed and configured
        else "Install the captions extra and set OPENREEL_WHISPER_MODEL to a local faster-whisper model directory.",
    }


def transcribe_audio(source: Path, duration: float, language: str | None = None) -> list[dict]:
    state = capabilities()
    if not state["available"]:
        raise ValueError(state["reason"])
    if not _inference.acquire(blocking=False):
        raise ValueError("A transcription is already running. Try again when it finishes.")
    try:
        with tempfile.TemporaryDirectory(prefix="openreel-captions-") as folder:
            audio = Path(folder) / "narration.wav"
            decode = subprocess.run(
                [
                    ffmpeg(),
                    "-nostdin",
                    "-v",
                    "error",
                    "-protocol_whitelist",
                    "file,pipe",
                    "-i",
                    str(source),
                    "-t",
                    str(duration),
                    "-map",
                    "0:a:0",
                    "-ar",
                    "16000",
                    "-ac",
                    "1",
                    str(audio),
                ],
                capture_output=True,
                timeout=120,
            )
            if decode.returncode:
                raise ValueError("The attached narration could not be decoded.")
            result = _run_whisper(audio, language)
            try:
                words = json.loads(result)
                if not isinstance(words, list) or len(words) > 20000:
                    raise ValueError("Expected a word list")
                return words
            except (ValueError, TypeError) as exc:
                raise ValueError("Whisper returned invalid word timestamps.") from exc
    except subprocess.TimeoutExpired as exc:
        raise ValueError(
            "Transcription exceeded its time limit. Use a smaller model or shorter narration."
        ) from exc
    finally:
        _inference.release()


def _run_whisper(audio: Path, language: str | None) -> bytes:
    # Spool subprocess output to disk and monitor its size instead of accumulating
    # potentially unbounded diagnostics or malformed output in the web process.
    with tempfile.TemporaryFile() as output, tempfile.TemporaryFile() as errors:
        process = subprocess.Popen(
            [sys.executable, "-m", "openreel.captions", str(audio), language or ""],
            stdout=output,
            stderr=errors,
        )
        started = time.monotonic()
        try:
            while process.poll() is None:
                if (
                    os.fstat(output.fileno()).st_size > 8 * 1024 * 1024
                    or os.fstat(errors.fileno()).st_size > 2 * 1024 * 1024
                ):
                    raise ValueError("Whisper output exceeded its size limit.")
                if time.monotonic() - started > 600:
                    raise subprocess.TimeoutExpired("Whisper", 600)
                time.sleep(0.1)
            if process.returncode:
                raise ValueError(
                    "Whisper could not transcribe this audio. Check the configured local model and compute settings."
                )
            output.seek(0)
            result = output.read(8 * 1024 * 1024 + 1)
            if len(result) > 8 * 1024 * 1024:
                raise ValueError("Whisper output exceeded its size limit.")
            return result
        finally:
            if process.poll() is None:
                process.kill()
            process.wait()


def _transcribe(source: str, language: str | None) -> list[dict]:
    from faster_whisper import WhisperModel

    model = WhisperModel(
        os.environ["OPENREEL_WHISPER_MODEL"],
        device=os.environ.get("OPENREEL_WHISPER_DEVICE", "cpu"),
        compute_type=os.environ.get("OPENREEL_WHISPER_COMPUTE_TYPE", "int8"),
        local_files_only=True,
        cpu_threads=2,
    )
    segments, _ = model.transcribe(source, language=language, word_timestamps=True, vad_filter=True)
    result = []
    for segment in segments:
        for word in segment.words or []:
            text = word.word.strip()
            if not text:
                continue
            if len(result) >= 20000 or len(text) > 200:
                raise ValueError("Whisper output exceeds the caption word limit.")
            result.append({"text": text, "start": word.start, "end": word.end})
    return result


if __name__ == "__main__":
    print(json.dumps(_transcribe(sys.argv[1], sys.argv[2] or None), ensure_ascii=False))
