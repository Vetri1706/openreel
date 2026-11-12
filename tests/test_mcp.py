import asyncio
import os
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


def test_stdio_protocol(tmp_path):
    async def run():
        params = StdioServerParameters(
            command=sys.executable,
            args=["-m", "openreel", "mcp", "--data-dir", str(tmp_path)],
            env=dict(os.environ),
        )
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                init = await session.initialize()
                assert init.serverInfo.name == "OpenReel"
                listing = await session.list_tools()
                assert {"create_project", "create_storyboard", "render_video", "generate_voice"} <= {
                    t.name for t in listing.tools
                }
                p = await session.call_tool("create_project", {"title": "From MCP"})
                assert not p.isError
                project = p.structuredContent
                result = await session.call_tool(
                    "create_storyboard",
                    {
                        "project_id": project["id"],
                        "expected_revision": project["revision"],
                        "scenes": [{"title": "First scene", "duration": 0.5, "title_card": "It works."}],
                    },
                )
                assert not result.isError
                job = await session.call_tool(
                    "render_video", {"project_id": project["id"], "resolution": "draft"}
                )
                assert not job.isError
                for _ in range(150):
                    status = await session.call_tool(
                        "get_render_status", {"job_id": job.structuredContent["id"]}
                    )
                    if status.structuredContent["status"] in {"completed", "failed"}:
                        assert status.structuredContent["status"] == "completed", status
                        break
                    await asyncio.sleep(0.2)
                else:
                    raise AssertionError("MCP render timed out")

    asyncio.run(run())


def test_streamable_http_protocol(client):
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
                "clientInfo": {"name": "test", "version": "1"},
            },
        },
    )
    assert init.status_code == 200, init.text
    assert init.json()["result"]["serverInfo"]["name"] == "OpenReel"
    headers["MCP-Protocol-Version"] = init.json()["result"]["protocolVersion"]
    result = client.post(
        "/mcp",
        headers=headers,
        json={
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": "create_project", "arguments": {"title": "HTTP MCP"}},
        },
    )
    assert result.status_code == 200, result.text
    assert not result.json()["result"].get("isError")
