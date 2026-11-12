import io

import pytest
from PIL import Image
from pydantic import ValidationError

from openreel.media import import_bytes
from openreel.models import Character, Project, Scene
from openreel.render import subtitles
from openreel.store import Conflict, Store


def png():
    out = io.BytesIO()
    Image.new("RGB", (80, 60), "#e9b9a6").save(out, "PNG")
    return out.getvalue()


def test_persistence_and_revision_conflict(service):
    project = service.create_project("A film")
    first = service.store.get(project.id)
    second = service.store.get(project.id)
    first.title = "New title"
    service.store.save(first)
    with pytest.raises(Conflict):
        service.store.save(second)
    assert Store(service.store.root).get(project.id).title == "New title"


def test_assets_are_project_scoped_and_typed(service):
    p = service.create_project("One")
    other = service.create_project("Two")
    a = import_bytes(service.store, p.id, "../../picture.png", png())
    assert a.name == "picture.png"
    assert service.store.asset_path(a.id).parent == service.store.root / "assets"
    other.scenes = [Scene(asset_id=a.id)]
    with pytest.raises(ValueError, match="another project"):
        service.store.save(other)
    p.scenes = [Scene(audio_asset_id=a.id)]
    with pytest.raises(ValueError, match="wrong media"):
        service.store.save(p)


def test_import_path_and_symlink_escape(service, tmp_path):
    p = service.create_project("Import")
    outside = tmp_path / "outside.png"
    outside.write_bytes(png())
    link = service.import_root / "link.png"
    link.symlink_to(outside)
    for path in (outside, link, service.import_root / "../outside.png"):
        with pytest.raises(ValueError, match="import root"):
            service.import_file(p.id, str(path))
    inside = service.import_root / "inside.png"
    inside.write_bytes(png())
    assert service.import_file(p.id, str(inside)).kind == "image"


def test_invalid_media_and_inputs(service, monkeypatch):
    monkeypatch.setenv("OPENREEL_MAX_SCENE_SECONDS", "60")
    monkeypatch.setenv("OPENREEL_MAX_DURATION_SECONDS", "600")
    p = service.create_project("Inputs")
    for name, data in [("bad.png", b"not a png"), ("bad.mp4", b"not video"), ("bad.html", b"<html>")]:
        with pytest.raises(ValueError):
            import_bytes(service.store, p.id, name, data)
    with pytest.raises(ValueError):
        service.upload_base64(p.id, "x.png", "notbase64$")
    for value in (float("nan"), float("inf"), -1, 61):
        with pytest.raises(ValidationError):
            Scene(duration=value)
    with pytest.raises(ValidationError):
        Project(title="Invalid", scenes=[Scene(character_ids=["missing"])])
    with pytest.raises(ValidationError):
        Project(title="Long", scenes=[Scene(duration=60) for _ in range(11)])


def test_storyboard_and_character_roundtrip(service):
    p = service.create_project("Characters")
    p = service.add_character(p.id, Character(id="tomato", name="Tomato", seed=19), p.revision)
    p = service.storyboard(p.id, [Scene(id="first", title="Hello", character_ids=["tomato"])], p.revision)
    p = service.update_scene(p.id, "first", {"subtitle": "வணக்கம்!", "duration": 2.5}, p.revision)
    assert p.scenes[0].subtitle == "வணக்கம்!"
    assert "00:00:02,500" in subtitles(p)
    with pytest.raises(Conflict):
        service.update_scene(p.id, "first", {"duration": 3}, p.revision - 1)


def test_api_validation_and_origin(client):
    assert client.get("/api/health").status_code == 200
    response = client.post("/api/projects", json={"title": "API film"})
    assert response.status_code == 200
    p = response.json()
    p["scenes"] = [Scene().model_dump()]
    assert client.put("/api/projects/" + p["id"], json=p).status_code == 200
    assert client.put("/api/projects/" + p["id"], json=p).status_code == 409
    assert client.get("/api/projects/does-not-exist").status_code == 404
    assert (
        client.post(
            "/api/projects", json={"title": "x"}, headers={"Origin": "https://evil.example"}
        ).status_code
        == 403
    )
    assert client.get("/api/projects", headers={"Host": "evil.example"}).status_code == 400
    assert (
        client.post("/api/projects", json={"title": "x"}, headers={"X-OpenReel-Request": ""}).status_code
        == 403
    )
    assert client.get("/api/assets/../../etc/passwd").status_code != 200


def test_media_upload(client):
    p = client.post("/api/projects", json={"title": "Media"}).json()
    result = client.post(f"/api/projects/{p['id']}/assets?filename=shot.png", content=png())
    assert result.status_code == 200
    a = result.json()
    assert client.get("/api/assets/" + a["id"]).content.startswith(b"\x89PNG")


def test_studio_utf8_and_oversized_stream_cleanup(client, service, monkeypatch):
    assert client.get("/").status_code == 200
    monkeypatch.setenv("OPENREEL_MAX_UPLOAD_MB", "1")
    project = service.create_project("Bounded upload")
    before = set((service.store.root / "scratch").iterdir())
    response = client.post(
        f"/api/projects/{project.id}/assets?filename=too-large.mp4",
        content=iter([b"x" * (600 * 1024), b"y" * (600 * 1024)]),
    )
    assert response.status_code == 413
    assert set((service.store.root / "scratch").iterdir()) == before
    assert service.store.assets(project.id) == []
