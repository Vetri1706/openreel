from typing import Any, Literal
from urllib.parse import urlsplit

from mcp.server.auth.settings import AuthSettings, ClientRegistrationOptions, RevocationOptions
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations

from .auth import OwnerOAuth
from .models import Character, Project, Scene
from .service import Service

INSTRUCTIONS = """You are the director; OpenReel is your local video production engine.
First get_capabilities, then create_project. Write scenes yourself and call create_storyboard.
Import real visuals, or use explicit title_card and color fields for typography videos.
OpenReel does NOT synthesize images or generative motion. Never claim it does.
Character descriptions, reference assets, and seeds are production notes, not an identity guarantee.
Import assets before assigning their IDs. Use expected_revision from the latest get_project
on every edit; on conflict read again and reconcile. Narration text is silent until generate_voice
or an imported audio asset is attached. Call render_video, poll get_render_status until completed,
then provide the actual video_path or video_url. Never claim an export succeeded before completion.
"""


def build_mcp(service: Service, oauth: OwnerOAuth | None = None):
    options = {}
    hosts = ["localhost", "localhost:*", "127.0.0.1", "127.0.0.1:*", "[::1]", "[::1]:*"]
    origins = ["http://localhost:*", "http://127.0.0.1:*", "http://[::1]:*"]
    if oauth:
        hosts.append(urlsplit(oauth.url).netloc)
        origins.append(oauth.url)
        options = dict(
            auth_server_provider=oauth,
            auth=AuthSettings(
                issuer_url=oauth.url,
                resource_server_url=oauth.resource,
                validate_token_resource=True,
                required_scopes=["studio"],
                client_registration_options=ClientRegistrationOptions(
                    enabled=True, valid_scopes=["studio"], default_scopes=["studio"]
                ),
                revocation_options=RevocationOptions(enabled=True),
            ),
        )
    mcp = FastMCP(
        "OpenReel",
        instructions=INSTRUCTIONS,
        json_response=True,
        stateless_http=True,
        max_request_body_size=12_000_000,
        transport_security=TransportSecuritySettings(allowed_hosts=hosts, allowed_origins=origins),
        **options,
    )
    read = ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=False)
    write = ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=False)
    edit = ToolAnnotations(readOnlyHint=False, destructiveHint=True, openWorldHint=False)

    @mcp.tool(annotations=read)
    def get_capabilities() -> dict[str, Any]:
        """Inspect installed rendering and Piper voice capabilities before planning a video."""
        capabilities = service.capabilities()
        if oauth:
            capabilities["local_file_import"] = False
        return capabilities

    @mcp.tool(annotations=read)
    def list_projects() -> list[dict[str, Any]]:
        """List locally stored video projects."""
        return service.store.projects()

    @mcp.tool(annotations=write)
    def create_project(
        title: str, brief: str = "", aspect_ratio: Literal["16:9", "9:16", "1:1"] = "16:9"
    ) -> Project:
        """Create an empty project. Write the storyboard using your own reasoning next."""
        return service.create_project(title, brief, aspect_ratio)

    @mcp.tool(annotations=read)
    def get_project(project_id: str) -> dict[str, Any]:
        """Read the current storyboard, revision, assets, characters, and export history."""
        return dict(
            project=service.store.get(project_id).model_dump(),
            assets=service.store.assets(project_id),
            renders=service.store.jobs(project_id),
        )

    @mcp.tool(annotations=edit)
    def create_storyboard(project_id: str, scenes: list[Scene], expected_revision: int) -> Project:
        """Replace the full storyboard with ordered scenes authored by you. Preserve IDs when editing."""
        return service.storyboard(project_id, scenes, expected_revision)

    @mcp.tool(annotations=edit)
    def update_scene(project_id: str, scene_id: str, changes: dict, expected_revision: int) -> Project:
        """Edit duration, visual_prompt, title_card, narration, subtitle, motion, transition, or asset IDs."""
        return service.update_scene(project_id, scene_id, changes, expected_revision)

    @mcp.tool(annotations=edit)
    def update_project(project: Project) -> Project:
        """Save a full project, including scene ordering, aspect ratio, characters, and music. Requires current revision."""
        return service.store.save(project)

    @mcp.tool(annotations=write)
    def add_character(project_id: str, character: Character, expected_revision: int) -> Project:
        """Save a character description, seed, and optional imported reference image for reuse."""
        return service.add_character(project_id, character, expected_revision)

    if service.import_root and not oauth:

        @mcp.tool(annotations=write)
        def import_asset(project_id: str, path: str) -> dict[str, Any]:
            """Copy an image, video, or audio file from the configured import root into a project."""
            return service.import_file(project_id, path).model_dump()

    @mcp.tool(annotations=write)
    def upload_asset(project_id: str, filename: str, content_base64: str) -> dict[str, Any]:
        """Upload up to 8 MB of base64 media. Assign the returned asset ID to a scene or character."""
        return service.upload_base64(project_id, filename, content_base64).model_dump()

    @mcp.tool(annotations=edit)
    def generate_voice(project_id: str, scene_id: str, voice: str, expected_revision: int) -> Project:
        """Synthesize scene narration with an installed Piper .onnx voice and attach the resulting WAV."""
        return service.generate_voice(project_id, scene_id, voice, expected_revision)

    @mcp.tool(annotations=write)
    def render_video(
        project_id: str, resolution: Literal["draft", "720p", "1080p"] = "720p"
    ) -> dict[str, Any]:
        """Queue an MP4 export of an immutable project snapshot. Poll get_render_status for completion."""
        return service.renderer.submit(project_id, resolution)

    @mcp.tool(annotations=read)
    def get_render_status(job_id: str) -> dict[str, Any]:
        """Read export progress, failure details, or the finished MP4 path and download URL."""
        job = service.store.job(job_id)
        if oauth:
            for key in ("video_url", "subtitles_url"):
                if key in job:
                    job[key] = oauth.url + job[key]
        return job

    @mcp.tool(annotations=edit)
    def cancel_render(job_id: str) -> dict[str, Any]:
        """Cancel a queued or active export. Finished exports are kept."""
        return service.renderer.cancel(job_id)

    @mcp.prompt()
    def direct_video(brief: str) -> str:
        """Plan, build, and export a video using the user's existing AI session."""
        return INSTRUCTIONS + "\nUser's video brief:\n" + brief

    return mcp
