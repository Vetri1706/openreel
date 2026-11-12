import hmac
import html
import time
from collections import defaultdict
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from mcp.server.auth.middleware.client_auth import AuthenticationError, ClientAuthenticator
from pydantic import ValidationError
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .auth import OwnerOAuth, digest
from .mcp_server import build_mcp
from .media import MAX_UPLOAD, import_bytes
from .models import Project, ProjectCreate, RenderRequest
from .render import subtitles
from .service import Service
from .store import Conflict

STATIC = Path(__file__).parent / "static"


def create_app(service: Service, public_url: str | None = None, owner_key: str | None = None):
    if public_url and (not owner_key or len(owner_key) < 32):
        raise ValueError("Remote access requires OPENREEL_OWNER_KEY with at least 32 characters.")
    if public_url and (urlsplit(public_url).scheme != "https" or urlsplit(public_url).path not in {"", "/"}):
        raise ValueError("Public URL must be an HTTPS origin with no path, such as https://reel.example.com.")
    oauth = OwnerOAuth(service.store, public_url) if public_url else None
    mcp = build_mcp(service, oauth)
    mcp_app = mcp.streamable_http_app()

    @asynccontextmanager
    async def lifespan(app):
        async with mcp.session_manager.run():
            yield

    app = FastAPI(
        title="OpenReel",
        version="0.1.0",
        lifespan=lifespan,
        docs_url=None if oauth else "/docs",
        redoc_url=None,
        openapi_url=None if oauth else "/openapi.json",
    )
    app.state.service = service
    app.state.oauth = oauth
    app.add_middleware(
        TrustedHostMiddleware,
        allowed_hosts=["localhost", "127.0.0.1", "[::1]"]
        + ([urlsplit(public_url).hostname] if public_url else []),
    )
    attempts = defaultdict(list)

    @app.middleware("http")
    async def protect(request: Request, call_next):
        origin = request.headers.get("origin")
        allowed = {str(request.base_url).rstrip("/")}
        if public_url:
            allowed.add(public_url.rstrip("/"))
        if origin and origin not in allowed:
            return JSONResponse({"detail": "Cross-origin requests are not allowed."}, status_code=403)
        if request.url.path.startswith("/api/") and request.url.path != "/api/health":
            auth = request.headers.get("authorization", "")
            if oauth:
                token = auth.removeprefix("Bearer ") if auth.startswith("Bearer ") else ""
                valid = hmac.compare_digest(token, owner_key) if token else False
                if not valid:
                    valid = bool(await oauth.load_access_token(token)) if token else False
                if not valid:
                    return JSONResponse(
                        {"detail": "Enter your server owner key to open the studio."}, status_code=401
                    )
            if (
                request.method not in {"GET", "HEAD"}
                and request.headers.get("x-openreel-request") != "studio"
                and not auth
            ):
                return JSONResponse({"detail": "Missing X-OpenReel-Request: studio header."}, status_code=403)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Cache-Control"] = "no-store"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
            "img-src 'self' blob: data:; media-src 'self' blob:; connect-src 'self'; "
            "font-src 'self'; frame-ancestors 'none'; base-uri 'none'; "
            + ("" if request.url.path == "/oauth/consent" else "form-action 'self'")
        )
        return response

    @app.exception_handler(KeyError)
    async def not_found(request, exc):
        return JSONResponse({"detail": str(exc).strip("'")}, status_code=404)

    @app.exception_handler(Conflict)
    async def conflict(request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=409)

    @app.exception_handler(ValueError)
    async def bad_request(request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=422)

    @app.exception_handler(ValidationError)
    async def invalid(request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=422)

    @app.get("/", response_class=HTMLResponse)
    def index():
        return (STATIC / "index.html").read_text()

    @app.get("/api/health")
    def health():
        return {"status": "ok", "version": "0.1.0", "authentication": bool(oauth)}

    @app.get("/api/capabilities")
    def capabilities():
        return service.capabilities()

    @app.get("/api/projects")
    def projects():
        return service.store.projects()

    @app.post("/api/projects")
    def create(body: ProjectCreate):
        return service.create_project(**body.model_dump())

    @app.post("/api/demo")
    def demo():
        return service.demo()

    @app.get("/api/projects/{project_id}")
    def project(project_id: str):
        return {
            "project": service.store.get(project_id),
            "assets": service.store.assets(project_id),
            "renders": service.store.jobs(project_id),
        }

    @app.put("/api/projects/{project_id}")
    def save(project_id: str, body: Project):
        if body.id != project_id:
            raise ValueError("Project ID cannot be changed.")
        return service.store.save(body)

    @app.post("/api/projects/{project_id}/assets")
    async def upload(project_id: str, request: Request, filename: str):
        if int(request.headers.get("content-length", 0)) > MAX_UPLOAD:
            return JSONResponse({"detail": "Maximum file size is 50 MB."}, status_code=413)
        data = bytearray()
        async for chunk in request.stream():
            data.extend(chunk)
            if len(data) > MAX_UPLOAD:
                return JSONResponse({"detail": "Maximum file size is 50 MB."}, status_code=413)
        from starlette.concurrency import run_in_threadpool

        return await run_in_threadpool(import_bytes, service.store, project_id, filename, bytes(data))

    @app.get("/api/assets/{asset_id}")
    def asset(asset_id: str):
        value = service.store.asset(asset_id)
        return FileResponse(
            service.store.asset_path(asset_id), filename=value.name, content_disposition_type="inline"
        )

    @app.post("/api/projects/{project_id}/renders")
    def render(project_id: str, body: RenderRequest):
        return service.renderer.submit(project_id, body.resolution)

    @app.get("/api/renders/{job_id}")
    def status(job_id: str):
        return service.store.job(job_id)

    @app.post("/api/renders/{job_id}/cancel")
    def cancel(job_id: str):
        return service.renderer.cancel(job_id)

    @app.get("/api/renders/{job_id}/{filename}")
    def download(job_id: str, filename: str):
        job = service.store.job(job_id)
        if job["status"] != "completed" or filename not in {"video.mp4", "subtitles.srt", "project.json"}:
            raise KeyError("Export is not available")
        return FileResponse(
            service.store.root / "renders" / job_id / filename,
            filename=filename,
            content_disposition_type="inline" if filename == "video.mp4" else "attachment",
        )

    @app.get("/api/projects/{project_id}/subtitles.srt")
    def captions(project_id: str):
        return Response(
            subtitles(service.store.get(project_id)),
            media_type="text/plain; charset=utf-8",
            headers={"Content-Disposition": 'attachment; filename="subtitles.srt"'},
        )

    if oauth:

        @app.post("/revoke")
        async def revoke(request: Request):
            # SDK 1.30's revoke form requires client_secret even for public clients.
            # Keep SDK authentication but allow that optional field to be omitted.
            try:
                client = await ClientAuthenticator(oauth).authenticate_request(request)
            except AuthenticationError:
                return JSONResponse({"error": "unauthorized_client"}, status_code=401)
            form = await request.form()
            token = str(form.get("token", ""))
            if not token:
                return JSONResponse({"error": "invalid_request"}, status_code=400)
            value = await oauth.load_access_token(token) or await oauth.load_refresh_token(client, token)
            if value and value.client_id == client.client_id:
                await oauth.revoke_token(value)
            return Response(status_code=200, headers={"Cache-Control": "no-store"})

        @app.get("/oauth/consent", response_class=HTMLResponse)
        def consent(ticket: str):
            flow = service.store.auth_get("consent", digest(ticket))
            if not flow:
                raise ValueError("Connection request expired. Start again in your AI client.")
            client = html.escape(flow["client_name"])
            redirect = html.escape(flow["params"]["redirect_uri"])
            safe_ticket = html.escape(ticket, quote=True)
            return f'''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width">
            <title>Connect OpenReel</title><link rel="stylesheet" href="/static/style.css">
            <main class="consent"><span class="wordmark">openreel<span>●</span></span>
            <h1>Give {client} a seat in your studio.</h1>
            <p>This client can read and edit all projects, upload media, and render videos on this server.</p>
            <p>Return address: <code>{redirect}</code></p>
            <form method="post" action="/oauth/consent"><input type="hidden" name="ticket" value="{safe_ticket}">
            <label>Server owner key<input type="password" name="key" required autocomplete="current-password"></label>
            <button class="primary" type="submit">Authorize this connection</button></form>
            <p>Close this page to decline. Only authorize a connection you started.</p></main></html>'''

        @app.post("/oauth/consent")
        async def approve(request: Request):
            address = request.client.host if request.client else "unknown"
            attempts[address] = [t for t in attempts[address] if t > time.time() - 60]
            if len(attempts[address]) >= 10:
                return JSONResponse({"detail": "Too many attempts. Wait one minute."}, status_code=429)
            attempts[address].append(time.time())
            form = await request.form()
            key, ticket = str(form.get("key", "")), str(form.get("ticket", ""))
            if not hmac.compare_digest(key, owner_key):
                return HTMLResponse("Owner key did not match. Go back and try again.", status_code=403)
            return RedirectResponse(oauth.approve(ticket), status_code=303)

    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    app.mount("/", mcp_app)
    return app
