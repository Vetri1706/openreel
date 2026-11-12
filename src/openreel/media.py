import io
import math
import os
import random
import shutil
import subprocess
from pathlib import Path

import imageio_ffmpeg
from PIL import Image, ImageDraw, ImageFont, ImageOps

from .config import limits
from .models import Asset, Scene, uid
from .store import Store

MAX_UPLOAD = limits().max_upload_bytes
Image.MAX_IMAGE_PIXELS = 40_000_000
FONTS = Path(__file__).parent / "static" / "fonts"


def ffmpeg() -> str:
    return os.environ.get("OPENREEL_FFMPEG") or shutil.which("ffmpeg") or imageio_ffmpeg.get_ffmpeg_exe()


def font(size: int, text: str = "", display=False):
    custom = os.environ.get("OPENREEL_FONT")
    name = (
        "notosanstamil"
        if any("\u0b80" <= ch <= "\u0bff" for ch in text)
        else ("spacegrotesk" if display else "dmsans")
    )
    return ImageFont.truetype(custom or str(FONTS / f"{name}.ttf"), size)


def import_bytes(store: Store, project_id: str, name: str, content: bytes) -> Asset:
    """Import a bounded in-memory response; large uploads should use import_file."""
    return _import_stream(store, project_id, name, io.BytesIO(content), len(content))


def import_file(store: Store, project_id: str, name: str, source: Path) -> Asset:
    """Validate and copy an uploaded or local file without loading it into memory."""
    with source.open("rb") as stream:
        return _import_stream(store, project_id, name, stream, source.stat().st_size)


def _import_stream(store: Store, project_id: str, name: str, stream, size: int) -> Asset:
    store.get(project_id)
    cap = limits().max_upload_bytes
    if not size or size > cap:
        raise ValueError(f"Choose a nonempty file no larger than {cap // (1024 * 1024)} MB.")
    ext = Path(name).suffix.lower()
    asset_id = uid()
    if ext in {".png", ".jpg", ".jpeg", ".webp"}:
        kind, ext = "image", ".png"
    elif ext in {".mp4", ".mov", ".webm", ".wav", ".mp3", ".m4a", ".ogg", ".flac"}:
        kind = "video" if ext in {".mp4", ".mov", ".webm"} else "audio"
    else:
        raise ValueError("Use PNG, JPEG, WebP, MP4, MOV, WebM, WAV, MP3, M4A, OGG, or FLAC.")
    path = store.root / "assets" / (asset_id + ext)
    try:
        if kind == "image":
            try:
                with Image.open(stream) as img:
                    img = ImageOps.exif_transpose(img).convert("RGB")
                    img.thumbnail((4096, 4096))
                    img.save(path, format="PNG")
            except Exception as exc:
                raise ValueError("This image could not be decoded.") from exc
        else:
            total = 0
            with path.open("wb") as dest:
                while chunk := stream.read(1024 * 1024):
                    total += len(chunk)
                    if total > cap:
                        raise ValueError("File exceeds the configured upload limit.")
                    dest.write(chunk)
            result = subprocess.run(
                [
                    ffmpeg(),
                    "-nostdin",
                    "-v",
                    "error",
                    "-protocol_whitelist",
                    "file,pipe",
                    "-i",
                    str(path),
                    "-map",
                    "0:v:0" if kind == "video" else "0:a:0",
                    "-t",
                    "0.05",
                    "-f",
                    "null",
                    "-",
                ],
                capture_output=True,
                timeout=25,
            )
            if result.returncode:
                raise ValueError("This media file could not be decoded as " + kind + ".")
        if path.stat().st_size > cap:
            raise ValueError("Decoded media exceeds the configured upload limit.")
        asset = Asset(
            id=asset_id,
            project_id=project_id,
            name=Path(name).name[:160],
            kind=kind,
            filename=path.name,
            size=path.stat().st_size,
        )
        store.add_asset(asset)
        return asset
    except BaseException:
        path.unlink(missing_ok=True)
        raise


def wrap(text: str, draw, face, width: int) -> list[str]:
    lines = []
    for paragraph in text.splitlines():
        line = ""
        for word in paragraph.split():
            candidate = f"{line} {word}".strip()
            if draw.textlength(candidate, font=face) <= width:
                line = candidate
                continue
            if line:
                lines.append(line)
            line = ""
            # Long unbroken words must also stay in frame.
            for char in word:
                if line and draw.textlength(line + char, font=face) > width:
                    lines.append(line)
                    line = ""
                line += char
        lines.append(line)
    return lines


def text_block(draw, text, box, size, fill="white", background=False, display=False):
    x0, y0, x1, y1 = box
    while True:
        face = font(size, text, display)
        lines = wrap(text, draw, face, x1 - x0 - 30)
        line_height = int(size * 1.45)
        if len(lines) * line_height <= y1 - y0 or size <= 10:
            break
        size -= 1
    y = y0 + ((y1 - y0) - len(lines) * line_height) / 2
    for line in lines:
        width = draw.textlength(line, font=face)
        x = (x0 + x1 - width) / 2
        if background and line:
            draw.rounded_rectangle(
                (x - 10, y - 4, x + width + 10, y + line_height), radius=6, fill=(9, 14, 16, 205)
            )
        draw.text((x, y), line, font=face, fill=fill)
        y += line_height


def overlay(scene: Scene, size: tuple[int, int], path: Path):
    w, h = size
    image = Image.new("RGBA", size)
    draw = ImageDraw.Draw(image)
    if scene.title_card:
        text_block(
            draw,
            scene.title_card,
            (w * 0.08, h * 0.18, w * 0.92, h * 0.65),
            int(min(w, h) * 0.095),
            display=True,
        )
    if scene.subtitle:
        text_block(
            draw,
            scene.subtitle,
            (w * 0.06, h * 0.73, w * 0.94, h * 0.92),
            int(min(w, h) * 0.045),
            background=True,
        )
    image.save(path)


def illustration(index: int, size=(1280, 720)) -> bytes:
    """Original, deterministic landscape illustrations for the starter project."""
    palettes = [
        ((23, 57, 63), (169, 191, 168), (221, 181, 127)),
        ((76, 47, 47), (225, 167, 130), (245, 215, 164)),
        ((28, 48, 62), (124, 167, 178), (214, 220, 194)),
        ((25, 44, 43), (158, 176, 145), (241, 212, 155)),
    ]
    dark, light, sun = palettes[index % len(palettes)]
    w, h = size
    im = Image.new("RGB", size)
    draw = ImageDraw.Draw(im)
    for y in range(h):
        t = y / h
        color = tuple(int(a * (1 - t) + b * t) for a, b in zip(dark, light))
        draw.line((0, y, w, y), fill=color)
    cx, cy, r = w * (0.68 if index % 2 == 0 else 0.32), h * 0.32, h * 0.105
    draw.ellipse((cx - r, cy - r, cx + r, cy + r), fill=sun)
    rng = random.Random(140 + index)
    for layer in range(5):
        base = h * (0.49 + layer * 0.09)
        points = [(0, h)]
        for x in range(-40, w + 80, 40):
            y = base + math.sin(x / (w * 0.16) + layer * 1.7 + index) * h * 0.075
            y += rng.uniform(-h * 0.025, h * 0.025)
            points.append((x, y))
        points.append((w, h))
        t = 0.28 + layer * 0.15
        col = tuple(int(a * t + b * (1 - t)) for a, b in zip(dark, light))
        draw.polygon(points, fill=col)
    out = io.BytesIO()
    im.save(out, "PNG")
    return out.getvalue()
