import base64
import io

from PIL import Image


def test_studio_request_agent_mcp_completion(client):
    project = client.post("/api/projects", json={"title": "Shared studio and agent workflow"}).json()
    project["scenes"] = [{"id": "shot", "title": "Generated scene"}]
    project = client.put(f"/api/projects/{project['id']}", json=project).json()
    request = client.post(
        f"/api/projects/{project['id']}/generations",
        json={
            "provider": "agent",
            "kind": "image",
            "prompt": "Original character reference",
            "scene_id": "shot",
            "expected_revision": project["revision"],
        },
    )
    assert request.status_code == 200, request.text
    job = request.json()
    assert job["status"] == "awaiting_agent"
    out = io.BytesIO()
    Image.new("RGB", (20, 20), "purple").save(out, "PNG")

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
                "clientInfo": {"name": "generation-integration-test", "version": "1"},
            },
        },
    )
    assert initialize.status_code == 200, initialize.text
    headers["MCP-Protocol-Version"] = initialize.json()["result"]["protocolVersion"]
    ready = client.post(
        "/mcp",
        headers=headers,
        json={
            "jsonrpc": "2.0",
            "method": "notifications/initialized",
            "params": {},
        },
    )
    assert ready.status_code == 202, ready.text
    request_id = 1

    def tool(name, arguments):
        nonlocal request_id
        request_id += 1
        result = client.post(
            "/mcp",
            headers=headers,
            json={
                "jsonrpc": "2.0",
                "id": request_id,
                "method": "tools/call",
                "params": {"name": name, "arguments": arguments},
            },
        )
        assert result.status_code == 200, result.text
        value = result.json()["result"]
        assert not value.get("isError"), value
        return value["structuredContent"]

    asset = tool(
        "upload_asset",
        {
            "project_id": project["id"],
            "filename": "actual.png",
            "content_base64": base64.b64encode(out.getvalue()).decode(),
        },
    )
    complete = tool(
        "complete_generation",
        {"job_id": job["id"], "asset_id": asset["id"], "provenance": {"producer": "test native media tool"}},
    )
    assert complete["status"] == "completed"
    assert complete["attached"] is True
    saved = client.get(f"/api/projects/{project['id']}").json()
    assert saved["project"]["scenes"][0]["asset_id"] == asset["id"]
    assert saved["generations"][0]["asset_id"] == asset["id"]
