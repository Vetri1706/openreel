"""Owner-configured resource limits shared by validation, uploads and workers."""

import os
from dataclasses import asdict, dataclass


def _integer(name: str, default: int, minimum: int, maximum: int) -> int:
    raw = os.environ.get(name, str(default))
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer.") from exc
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}.")
    return value


@dataclass(frozen=True)
class Limits:
    max_duration_seconds: int
    max_scenes: int
    max_scene_seconds: int
    max_upload_bytes: int
    max_queued_renders: int
    render_stage_timeout_seconds: int

    def as_dict(self) -> dict:
        return asdict(self)


def limits() -> Limits:
    return Limits(
        max_duration_seconds=_integer("OPENREEL_MAX_DURATION_SECONDS", 7200, 1, 86400),
        max_scenes=_integer("OPENREEL_MAX_SCENES", 1000, 1, 10000),
        max_scene_seconds=_integer("OPENREEL_MAX_SCENE_SECONDS", 600, 1, 86400),
        max_upload_bytes=_integer("OPENREEL_MAX_UPLOAD_MB", 500, 1, 10240) * 1024 * 1024,
        max_queued_renders=_integer("OPENREEL_MAX_QUEUED_RENDERS", 32, 1, 10000),
        render_stage_timeout_seconds=_integer("OPENREEL_RENDER_STAGE_TIMEOUT_SECONDS", 3600, 1, 86400),
    )
