import io
import subprocess
import time
import wave
from pathlib import Path

import imageio_ffmpeg
import pytest
from PIL import Image

from openreel.media import ffmpeg, import_bytes
from openreel.models import Scene


def wait(service, job):
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        status = service.store.job(job["id"])
        if status["status"] in {"completed", "failed", "canceled"}:
            return status
        time.sleep(0.1)
    pytest.fail("Render did not finish in 90 seconds")


def sound():
    out = io.BytesIO()
    with wave.open(out, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16000)
        wav.writeframes(b"\x00\x00" * 16000)
    return out.getvalue()


@pytest.mark.parametrize("aspect", ["16:9", "9:16", "1:1"])
def test_real_mp4_images_audio_subtitles(service, aspect):
    p = service.create_project("Render", aspect_ratio=aspect)
    img = io.BytesIO()
    Image.new("RGB", (400, 300), "#446a6c").save(img, "PNG")
    visual = import_bytes(service.store, p.id, "image.png", img.getvalue())
    voice = import_bytes(service.store, p.id, "voice.wav", sound())
    p.scenes = [
        Scene(
            title_card="OpenReel",
            duration=0.75,
            asset_id=visual.id,
            subtitle="வணக்கம்!",
            audio_asset_id=voice.id,
        ),
        Scene(duration=0.75, title_card="Your story.", motion="pan_right", transition="cut"),
    ]
    p.music_asset_id = voice.id
    service.store.save(p)
    job = wait(service, service.renderer.submit(p.id, "draft"))
    assert job["status"] == "completed", job.get("error")
    video = Path(job["video_path"])
    reader = imageio_ffmpeg.read_frames(str(video))
    meta = next(reader)
    assert meta["size"] == {"16:9": (640, 360), "9:16": (360, 640), "1:1": (360, 360)}[aspect]
    assert meta["fps"] == 24
    assert 1.4 < meta["duration"] < 1.7
    assert len(next(reader)) > 1000
    reader.close()
    assert "வணக்கம்!" in video.with_name("subtitles.srt").read_text(encoding="utf-8")
    assert video.with_name("project.json").exists()
    decoded = subprocess.run(
        [ffmpeg(), "-v", "error", "-i", str(video), "-map", "0:a:0", "-f", "null", "-"], capture_output=True
    )
    assert decoded.returncode == 0


def test_video_clip_and_cancel(service):
    p = service.create_project("Clip")
    clip = service.store.root / "scratch" / "source.mp4"
    subprocess.run(
        [
            ffmpeg(),
            "-v",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=red:s=160x90:d=0.5",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(clip),
        ],
        check=True,
    )
    asset = import_bytes(service.store, p.id, "clip.mp4", clip.read_bytes())
    p.scenes = [Scene(asset_id=asset.id, duration=1, subtitle="A real clip")]
    service.store.save(p)
    assert wait(service, service.renderer.submit(p.id, "draft"))["status"] == "completed"
    job = service.renderer.submit(p.id, "1080p")
    service.renderer.cancel(job["id"])
    assert wait(service, job)["status"] == "canceled"
    assert not (service.store.root / "renders" / job["id"] / "video.mp4").exists()
