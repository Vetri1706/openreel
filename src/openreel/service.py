import base64
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from . import captions
from .config import limits
from .generation import GenerationManager
from .media import ffmpeg, illustration, import_bytes, import_file
from .models import CaptionWord, Character, Project, Scene
from .render import Renderer
from .store import Store


class Service:
    def __init__(self, root: Path, import_root: Path | None = None, render_worker: bool | None = None):
        self.store = Store(root)
        self.generation = GenerationManager(self.store)
        try:
            self.renderer = Renderer(self.store, autostart=render_worker)
        except BaseException:
            self.generation.shutdown(wait=True)
            raise
        self.import_root = import_root.resolve() if import_root else None

    def capabilities(self) -> dict:
        model_dir = Path(os.environ.get("OPENREEL_PIPER_MODELS", "/nonexistent"))
        generation = self.generation.capabilities()
        return dict(
            version="0.1.0",
            renderer="FFmpeg",
            ffmpeg_available=Path(ffmpeg()).is_file(),
            piper_available=bool(shutil.which("piper")),
            voices=[p.name for p in model_dir.glob("*.onnx")],
            local_file_import=bool(self.import_root),
            image_generation=any(
                p["configured"] and p["id"] != "agent" and "image" in p["kinds"]
                for p in generation["providers"]
            ),
            generative_video=any(
                p["configured"] and p["id"] != "agent" and "video" in p["kinds"]
                for p in generation["providers"]
            ),
            generation=generation,
            transcription=captions.capabilities(),
            limits=limits().as_dict(),
            description="Generate through configured ComfyUI workflows or a connected agent, then compose and render locally.",
        )

    def close(self):
        try:
            self.generation.shutdown(wait=True)
        finally:
            self.renderer.pool.shutdown(wait=True)

    def transcribe_scene(
        self, project_id: str, scene_id: str, expected_revision: int, language: str | None = None
    ):
        from .store import Conflict

        project = self.store.get(project_id)
        if project.revision != expected_revision:
            raise Conflict("Project changed. Reload before transcribing.")
        scene = next((s for s in project.scenes if s.id == scene_id), None)
        if scene is None:
            raise KeyError("Scene not found")
        if not scene.audio_asset_id:
            raise ValueError("Attach narration audio to this scene before transcribing.")
        self.store.validate_assets(project)
        raw = captions.transcribe_audio(self.store.asset_path(scene.audio_asset_id), scene.duration, language)
        words = [CaptionWord.model_validate(word) for word in raw]
        # Audio is trimmed to the shot. Rounding at the last timestamp may exceed its duration.
        words = [
            word.model_copy(update={"end": min(word.end, scene.duration)})
            for word in words
            if word.start < scene.duration and word.end > word.start
        ]
        if not words:
            raise ValueError("No speech was detected in this scene's narration. Existing captions were kept.")
        scene.caption_words = words
        return self.store.save(project)

    def create_project(self, title: str, brief: str = "", aspect_ratio: str = "16:9") -> Project:
        return self.store.create(Project(title=title, brief=brief, aspect_ratio=aspect_ratio))

    def storyboard(self, project_id: str, scenes: list[Scene], expected_revision: int) -> Project:
        project = self.store.get(project_id)
        project.revision = expected_revision
        project.scenes = scenes
        return self.store.save(project)

    def update_scene(self, project_id: str, scene_id: str, changes: dict, expected_revision: int) -> Project:
        project = self.store.get(project_id)
        project.revision = expected_revision
        for i, scene in enumerate(project.scenes):
            if scene.id == scene_id:
                if "id" in changes:
                    raise ValueError("Scene IDs cannot be edited.")
                project.scenes[i] = Scene.model_validate({**scene.model_dump(), **changes})
                return self.store.save(project)
        raise KeyError("Scene not found")

    def add_character(self, project_id: str, character: Character, expected_revision: int) -> Project:
        project = self.store.get(project_id)
        project.revision = expected_revision
        project.characters.append(character)
        return self.store.save(project)

    def import_file(self, project_id: str, path: str):
        if not self.import_root:
            raise ValueError("Local file import is disabled. Start with --import-root or use upload_asset.")
        resolved = Path(path).expanduser().resolve()
        if not resolved.is_relative_to(self.import_root) or not resolved.is_file():
            raise ValueError("Choose a file inside the configured import root.")
        return import_file(self.store, project_id, resolved.name, resolved)

    def upload_base64(self, project_id: str, filename: str, content_base64: str):
        if len(content_base64) > 4 * ((8 * 1024 * 1024 + 2) // 3):
            raise ValueError("MCP uploads support up to 8 MB; use the studio for larger files.")
        try:
            data = base64.b64decode(content_base64, validate=True)
        except ValueError as exc:
            raise ValueError("Invalid base64 media content.") from exc
        if len(data) > 8 * 1024 * 1024:
            raise ValueError("MCP uploads support up to 8 MB; use the studio for larger files.")
        return import_bytes(self.store, project_id, filename, data)

    def generate_voice(self, project_id: str, scene_id: str, voice: str, expected_revision: int):
        project = self.store.get(project_id)
        if project.revision != expected_revision:
            from .store import Conflict

            raise Conflict("Project changed. Reload before generating voice.")
        scene = next((s for s in project.scenes if s.id == scene_id), None)
        if not scene:
            raise KeyError("Scene not found")
        if not scene.narration.strip():
            raise ValueError("Write scene narration first.")
        model_dir = Path(os.environ.get("OPENREEL_PIPER_MODELS", "/nonexistent")).resolve()
        model = (model_dir / voice).resolve()
        if not model.is_relative_to(model_dir) or not model.is_file() or model.suffix != ".onnx":
            raise ValueError("Choose a Piper .onnx voice from OPENREEL_PIPER_MODELS.")
        binary = shutil.which("piper")
        if not binary:
            raise ValueError("Install Piper TTS and add piper to PATH. See docs/audio.md.")
        with tempfile.TemporaryDirectory(dir=self.store.root / "scratch") as tmp:
            wav = Path(tmp) / "voice.wav"
            result = subprocess.run(
                [binary, "--model", str(model), "--output_file", str(wav)],
                input=scene.narration,
                text=True,
                capture_output=True,
                timeout=180,
            )
            if result.returncode or not wav.is_file():
                raise ValueError(
                    "Piper could not synthesize the voice. Check the model and its .onnx.json file."
                )
            asset = import_bytes(self.store, project_id, "narration.wav", wav.read_bytes())
        scene.audio_asset_id = asset.id
        return self.store.save(project)

    def demo(self) -> Project:
        project = self.create_project(
            "Somewhere, slower",
            "A short visual escape. Original illustrated landscapes, quiet pacing, and room to breathe.",
        )
        titles = [
            "Leave the noise behind",
            "Take the longer way",
            "Find a little perspective",
            "Make room for wonder",
        ]
        captions = [
            "A little further from the everyday.",
            "There is no hurry here.",
            "Some things are better seen slowly.",
            "Your next story starts here.",
        ]
        for i, title in enumerate(titles):
            asset = import_bytes(self.store, project.id, f"landscape-{i + 1}.png", illustration(i))
            project.scenes.append(
                Scene(
                    title=title,
                    duration=4,
                    asset_id=asset.id,
                    visual_prompt="Layered mountain silhouettes beneath a quiet sun. Original procedural illustration.",
                    title_card="Somewhere,\nslower." if i == 0 else title,
                    subtitle=captions[i],
                    motion=["push_in", "pan_right", "pull_out", "push_in"][i],
                )
            )
        return self.store.save(project)
