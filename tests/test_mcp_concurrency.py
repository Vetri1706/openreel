"""Long native tools must not block the shared studio/MCP event loop."""

import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from openreel.models import Asset


@pytest.mark.parametrize(
    "tool,method,arguments,is_asset",
    [
        ("transcribe_scene", "transcribe_scene", {"scene_id": "shot", "expected_revision": 1}, False),
        (
            "generate_voice",
            "generate_voice",
            {"scene_id": "shot", "voice": "local.onnx", "expected_revision": 1},
            False,
        ),
        ("upload_asset", "upload_base64", {"filename": "image.png", "content_base64": "AAAA"}, True),
        ("import_asset", "import_file", {"path": "inside-import-root.png"}, True),
    ],
)
def test_blocking_media_tool_keeps_studio_responsive(
    client, service, monkeypatch, tool, method, arguments, is_asset
):
    project = service.create_project("Concurrent MCP")
    started, release = threading.Event(), threading.Event()
    asset = Asset(project_id=project.id, name="image.png", kind="image", filename="example.png", size=1)

    def slow_operation(*args):
        started.set()
        assert release.wait(5), "Test did not release the simulated native operation"
        return asset if is_asset else project

    monkeypatch.setattr(service, method, slow_operation)
    headers = {"Accept": "application/json, text/event-stream"}
    init = client.post(
        "/mcp",
        headers=headers,
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-03-26",
                "capabilities": {},
                "clientInfo": {"name": "concurrency-test", "version": "1"},
            },
        },
    )
    assert init.status_code == 200, init.text
    headers["MCP-Protocol-Version"] = init.json()["result"]["protocolVersion"]
    with ThreadPoolExecutor(max_workers=2) as pool:
        operation = pool.submit(
            client.post,
            "/mcp",
            headers=headers,
            json={
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {"name": tool, "arguments": {"project_id": project.id, **arguments}},
            },
        )
        try:
            assert started.wait(3), "The native operation did not start"
            health = pool.submit(client.get, "/api/health").result(timeout=2)
            assert health.status_code == 200
            assert not operation.done(), (
                "The health request must finish while the media tool is still blocked"
            )
        finally:
            release.set()
        response = operation.result(timeout=3)
        assert response.status_code == 200, response.text
        assert not response.json()["result"].get("isError"), response.text


def test_agent_completion_schema_lists_supported_provenance(client):
    result = client.post(
        "/mcp",
        headers={"Accept": "application/json, text/event-stream"},
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/list",
            "params": {},
        },
    )
    assert result.status_code == 200, result.text
    tool = next(tool for tool in result.json()["result"]["tools"] if tool["name"] == "complete_generation")
    schema = tool["inputSchema"]["$defs"]["AgentProvenance"]
    assert set(schema["properties"]) == {"producer", "model", "notes"}
    assert schema["additionalProperties"] is False
