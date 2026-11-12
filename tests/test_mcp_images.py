import base64
import io

import pytest
from PIL import Image

from openreel.media import import_bytes
from openreel.models import Asset


def call_image(client, asset_id):
    headers = {"Accept": "application/json, text/event-stream"}
    initialize = client.post(
        "/mcp",
        headers=headers,
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-03-26",
                "capabilities": {},
                "clientInfo": {"name": "image-reference-test", "version": "1"},
            },
        },
    )
    assert initialize.status_code == 200
    headers["MCP-Protocol-Version"] = initialize.json()["result"]["protocolVersion"]
    assert (
        client.post(
            "/mcp",
            headers=headers,
            json={
                "jsonrpc": "2.0",
                "method": "notifications/initialized",
                "params": {},
            },
        ).status_code
        == 202
    )
    response = client.post(
        "/mcp",
        headers=headers,
        json={
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": "read_image_asset", "arguments": {"asset_id": asset_id}},
        },
    )
    assert response.status_code == 200, response.text
    return response.json()["result"]


def test_read_reference_returns_real_bounded_image_content(client, service):
    project = service.create_project("Reference")
    source = Image.new("RGB", (2048, 1024), "red")
    source.paste("blue", (1024, 0, 2048, 1024))
    data = io.BytesIO()
    source.save(data, "PNG")
    asset = import_bytes(service.store, project.id, "reference.png", data.getvalue())
    original = service.store.asset_path(asset.id).read_bytes()
    result = call_image(client, asset.id)
    assert not result.get("isError"), result
    content = next(item for item in result["content"] if item["type"] == "image")
    assert content["mimeType"] == "image/jpeg"
    thumbnail = base64.b64decode(content["data"], validate=True)
    assert len(thumbnail) <= 1024 * 1024
    with Image.open(io.BytesIO(thumbnail)) as actual:
        assert actual.size == (1024, 512)
        left, right = actual.getpixel((100, 100)), actual.getpixel((900, 100))
        assert left[0] > 240 and left[2] < 20
        assert right[2] > 240 and right[0] < 20
    assert service.store.asset_path(asset.id).read_bytes() == original


@pytest.mark.parametrize("kind", ["video", "audio"])
def test_read_reference_rejects_nonimage_before_file_access(client, service, kind):
    project = service.create_project("No image")
    asset = Asset(project_id=project.id, name="media", kind=kind, filename="must-not-open", size=1)
    service.store.add_asset(asset)
    result = call_image(client, asset.id)
    assert result["isError"]
    assert "image assets only" in result["content"][0]["text"]


@pytest.mark.parametrize("asset_id", ["../../secret.png", "C:\\Windows\\win.ini", "not-an-asset"])
def test_read_reference_rejects_paths(client, asset_id):
    result = call_image(client, asset_id)
    assert result["isError"]
    assert "asset ID" in result["content"][0]["text"]


def test_read_reference_requires_a_known_asset(client):
    result = call_image(client, "f" * 32)
    assert result["isError"]
    assert "Asset not found" in result["content"][0]["text"]
