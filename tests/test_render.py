import io
import math
import subprocess
import time
import wave
from array import array
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


@pytest.mark.parametrize("with_music", [False, True])
def test_audio_stays_continuous_across_scene_cuts(service, with_music):
    """AAC priming must not introduce fresh dropouts at each joined scene."""
    sample_rate = 48000
    tone = array(
        "h",
        (
            round(0.3 * 32767 * math.sin(2 * math.pi * 1000 * i / sample_rate))
            for i in range(sample_rate // 2)
        ),
    )
    content = io.BytesIO()
    with wave.open(content, "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(sample_rate)
        output.writeframes(tone.tobytes())
    project = service.create_project("Continuous audio across four cuts")
    voice = import_bytes(service.store, project.id, "tone.wav", content.getvalue())
    # 0.51 rounds to 12 frames at 24 fps. Each half-second contains 500 full
    # cycles, so repeated source files have no intended boundary discontinuity.
    project.scenes = [
        Scene(duration=0.51, audio_asset_id=voice.id, transition="cut", motion="still") for _ in range(5)
    ]
    if with_music:
        music = import_bytes(service.store, project.id, "silent-music.wav", sound())
        project.music_asset_id = music.id
    service.store.save(project)
    job = wait(service, service.renderer.submit(project.id, "draft"))
    assert job["status"] == "completed", job.get("error")
    decoded = subprocess.run(
        [
            ffmpeg(),
            "-v",
            "error",
            "-i",
            job["video_path"],
            "-map",
            "0:a:0",
            "-af",
            "aresample=async=1:min_hard_comp=0.001:first_pts=0",
            "-ac",
            "1",
            "-ar",
            str(sample_rate),
            "-f",
            "f32le",
            "pipe:1",
        ],
        capture_output=True,
        check=True,
    )
    samples = array("f")
    samples.frombytes(decoded.stdout)

    def rms(start, duration=0.01):
        window = samples[round(start * sample_rate) : round((start + duration) * sample_rate)]
        assert len(window) == round(duration * sample_rate)
        return math.sqrt(sum(value * value for value in window) / len(window))

    reference = rms(0.25, 0.1)
    assert reference > 0.1, "The output must contain the actual tone"
    for cut in (0.5, 1.0, 1.5, 2.0):
        # Honor presentation timestamps instead of flattening AAC packets.
        # Include possible fixed encoder/limiter latency on either side of cuts.
        minimum_ratio = min(rms(cut - 0.08 + step * 0.005) / reference for step in range(40))
        assert minimum_ratio > 0.7, f"Audio dropout near {cut}s: RMS ratio {minimum_ratio:.3f}"
