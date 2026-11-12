import io
import wave
from types import SimpleNamespace

import pytest

from openreel import captions
from openreel.media import import_bytes
from openreel.models import Scene
from openreel.store import Conflict


def scene_with_audio(service):
    project = service.create_project("Transcription")
    content = io.BytesIO()
    with wave.open(content, "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(16000)
        output.writeframes(b"\x00\x00" * 16000)
    audio = import_bytes(service.store, project.id, "narration.wav", content.getvalue())
    project.scenes = [Scene(id="shot", duration=1, audio_asset_id=audio.id, subtitle="Original")]
    return service.store.save(project)


def test_transcribe_attached_audio_and_conflicting_edit(service, monkeypatch):
    project = scene_with_audio(service)
    words = [{"text": "Hello", "start": 0.2, "end": 0.5}, {"text": "world", "start": 0.5, "end": 1.01}]
    monkeypatch.setattr(captions, "transcribe_audio", lambda *args: words)
    updated = service.transcribe_scene(project.id, "shot", project.revision)
    assert updated.scenes[0].caption_words[-1].end == 1
    assert updated.scenes[0].subtitle == "Original"

    def concurrent_edit(*args):
        latest = service.store.get(project.id)
        latest.title = "Edited during transcription"
        service.store.save(latest)
        return [{"text": "Replacement", "start": 0, "end": 0.3}]

    monkeypatch.setattr(captions, "transcribe_audio", concurrent_edit)
    with pytest.raises(Conflict):
        service.transcribe_scene(project.id, "shot", updated.revision)
    assert service.store.get(project.id).scenes[0].caption_words[0].text == "Hello"


def test_transcription_preserves_captions_on_silence_and_invalid_timing(service, monkeypatch):
    project = scene_with_audio(service)
    for words in (
        [],
        [{"text": "bad", "start": -1, "end": 0.3}],
        [{"text": "bad", "start": 0, "end": float("nan")}],
    ):
        monkeypatch.setattr(captions, "transcribe_audio", lambda *args: words)
        with pytest.raises(ValueError):
            service.transcribe_scene(project.id, "shot", project.revision)
        assert service.store.get(project.id).revision == project.revision


def test_unavailable_transcription_and_rest_validation(client, service, monkeypatch):
    monkeypatch.delenv("OPENREEL_WHISPER_MODEL", raising=False)
    project = scene_with_audio(service)
    endpoint = f"/api/projects/{project.id}/scenes/shot/transcribe"
    result = client.post(endpoint, json={"expected_revision": project.revision})
    assert result.status_code == 422
    assert "local faster-whisper" in result.json()["detail"]
    assert (
        client.post(
            endpoint, json={"expected_revision": project.revision, "language": "../model"}
        ).status_code
        == 422
    )


def test_whisper_requests_real_word_timestamps_without_downloads(monkeypatch):
    import sys

    seen = {}

    class FakeModel:
        def __init__(self, model, **kwargs):
            seen.update(model=model, **kwargs)

        def transcribe(self, source, **kwargs):
            seen.update(source=source, **kwargs)
            return iter([SimpleNamespace(words=[SimpleNamespace(word=" hello ", start=0.1, end=0.4)])]), None

    monkeypatch.setitem(sys.modules, "faster_whisper", SimpleNamespace(WhisperModel=FakeModel))
    monkeypatch.setenv("OPENREEL_WHISPER_MODEL", "/local/model")
    assert captions._transcribe("clip.wav", "en") == [{"text": "hello", "start": 0.1, "end": 0.4}]
    assert seen["word_timestamps"] is True
    assert seen["local_files_only"] is True


def test_whisper_rejects_excessive_output_before_serializing(monkeypatch):
    import sys

    class ExcessiveModel:
        def __init__(self, *args, **kwargs):
            pass

        def transcribe(self, *args, **kwargs):
            words = (SimpleNamespace(word="word", start=0, end=1) for _ in range(20001))
            return iter([SimpleNamespace(words=words)]), None

    monkeypatch.setitem(sys.modules, "faster_whisper", SimpleNamespace(WhisperModel=ExcessiveModel))
    monkeypatch.setenv("OPENREEL_WHISPER_MODEL", "/local/model")
    with pytest.raises(ValueError, match="word limit"):
        captions._transcribe("clip.wav", None)
