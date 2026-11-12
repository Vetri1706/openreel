from datetime import datetime, timezone
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .config import limits


def uid() -> str:
    return uuid4().hex


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class Character(Model):
    id: str = Field(default_factory=uid, pattern=r"^[a-zA-Z0-9_-]{1,64}$")
    name: str = Field(min_length=1, max_length=80)
    description: str = Field(default="", max_length=2000)
    reference_asset_id: str | None = None
    seed: int = Field(default=42, ge=0, le=2**32 - 1)


class CaptionWord(Model):
    text: str = Field(min_length=1, max_length=200)
    start: float = Field(ge=0)
    end: float = Field(gt=0)

    @model_validator(mode="after")
    def validate_timing(self):
        if not self.text.strip() or self.end <= self.start:
            raise ValueError("Caption words need text and an end after their start.")
        return self


class Scene(Model):
    id: str = Field(default_factory=uid, pattern=r"^[a-zA-Z0-9_-]{1,64}$")
    title: str = Field(default="Untitled scene", min_length=1, max_length=120)
    duration: float = Field(default=4, ge=0.5)
    visual_prompt: str = Field(default="", max_length=4000)
    narration: str = Field(default="", max_length=2000)
    subtitle: str = Field(default="", max_length=400)
    caption_words: list[CaptionWord] = Field(default_factory=list, max_length=20000)
    title_card: str = Field(default="", max_length=160)
    color: str = Field(default="#233a3e", pattern=r"^#[0-9a-fA-F]{6}$")
    motion: Literal["still", "push_in", "pull_out", "pan_left", "pan_right"] = "push_in"
    transition: Literal["cut", "fade"] = "fade"
    asset_id: str | None = None
    audio_asset_id: str | None = None
    character_ids: list[str] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def validate_scene(self):
        if self.duration > limits().max_scene_seconds:
            raise ValueError(f"Scenes may last at most {limits().max_scene_seconds} seconds.")
        previous_end = 0.0
        for word in self.caption_words:
            if word.start < previous_end or word.end > self.duration:
                raise ValueError("Caption words must be ordered, non-overlapping, and inside the scene.")
            previous_end = word.end
        return self


class Project(Model):
    id: str = Field(default_factory=uid, pattern=r"^[a-f0-9]{32}$")
    title: str = Field(min_length=1, max_length=120)
    brief: str = Field(default="", max_length=5000)
    aspect_ratio: Literal["16:9", "9:16", "1:1"] = "16:9"
    fps: Literal[24, 30] = 24
    scenes: list[Scene] = Field(default_factory=list)
    characters: list[Character] = Field(default_factory=list, max_length=30)
    music_asset_id: str | None = None
    music_volume: float = Field(default=0.18, ge=0, le=1)
    revision: int = Field(default=1, ge=1)
    created_at: str = Field(default_factory=now)
    updated_at: str = Field(default_factory=now)

    @model_validator(mode="after")
    def validate_timeline(self):
        cap = limits()
        if len(self.scenes) > cap.max_scenes:
            raise ValueError(f"Projects may contain at most {cap.max_scenes} scenes.")
        if sum(s.duration for s in self.scenes) > cap.max_duration_seconds:
            raise ValueError(f"Projects may contain at most {cap.max_duration_seconds} seconds of video.")
        if len({s.id for s in self.scenes}) != len(self.scenes):
            raise ValueError("Scene IDs must be unique.")
        if len({c.id for c in self.characters}) != len(self.characters):
            raise ValueError("Character IDs must be unique.")
        ids = {c.id for c in self.characters}
        if any(set(s.character_ids) - ids for s in self.scenes):
            raise ValueError("A scene references an unknown character.")
        return self


class ProjectCreate(Model):
    title: str = Field(min_length=1, max_length=120)
    brief: str = Field(default="", max_length=5000)
    aspect_ratio: Literal["16:9", "9:16", "1:1"] = "16:9"


class RenderRequest(Model):
    resolution: Literal["draft", "720p", "1080p"] = "720p"


class Asset(Model):
    id: str = Field(default_factory=uid)
    project_id: str
    name: str
    kind: Literal["image", "video", "audio"]
    filename: str
    size: int


def dimensions(aspect: str, resolution: str) -> tuple[int, int]:
    short = {"draft": 360, "720p": 720, "1080p": 1080}[resolution]
    long = short * 16 // 9
    return {"16:9": (long, short), "9:16": (short, long), "1:1": (short, short)}[aspect]
