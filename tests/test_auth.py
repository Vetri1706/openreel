import base64
import hashlib
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient

from openreel.web import create_app


def test_remote_requires_strong_key(service):
    with pytest.raises(ValueError, match="32 characters"):
        create_app(service, "https://reel.example", "weak")


def test_oauth_pkce_consent_replay_revocation(service):
    owner = "a-test-owner-key-with-at-least-32-characters"
    app = create_app(service, "https://reel.example", owner)
    with TestClient(app, base_url="https://reel.example") as client:
        assert client.get("/api/projects").status_code == 401
        assert client.get("/api/projects", headers={"Authorization": "Bearer " + owner}).status_code == 200
        metadata = client.get("/.well-known/oauth-authorization-server")
        assert metadata.status_code == 200
        assert metadata.json()["code_challenge_methods_supported"] == ["S256"]
        registered = client.post(
            "/register",
            json={
                "client_name": "Test Director",
                "redirect_uris": ["https://client.example/callback"],
                "token_endpoint_auth_method": "none",
                "grant_types": ["authorization_code", "refresh_token"],
                "response_types": ["code"],
            },
        )
        assert registered.status_code == 201, registered.text
        client_id = registered.json()["client_id"]
        verifier = "v" * 64
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        params = {
            "client_id": client_id,
            "redirect_uri": "https://client.example/callback",
            "response_type": "code",
            "state": "state123",
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "scope": "studio",
            "resource": "https://reel.example/mcp",
        }
        denied = client.get(
            "/authorize", params={**params, "redirect_uri": "https://evil.example"}, follow_redirects=False
        )
        assert denied.status_code == 400
        auth = client.get("/authorize", params=params, follow_redirects=False)
        assert auth.status_code in (302, 303, 307), auth.text
        consent_url = auth.headers["location"]
        ticket = parse_qs(urlparse(consent_url).query)["ticket"][0]
        assert "Test Director" in client.get(consent_url).text
        assert client.post("/oauth/consent", data={"ticket": ticket, "key": "wrong"}).status_code == 403
        approved = client.post(
            "/oauth/consent", data={"ticket": ticket, "key": owner}, follow_redirects=False
        )
        assert approved.status_code == 303
        query = parse_qs(urlparse(approved.headers["location"]).query)
        assert query["state"] == ["state123"]
        code = query["code"][0]
        data = {
            "grant_type": "authorization_code",
            "client_id": client_id,
            "code": code,
            "code_verifier": verifier,
            "redirect_uri": "https://client.example/callback",
            "resource": "https://reel.example/mcp",
        }
        assert client.post("/token", data={**data, "code_verifier": "wrong"}).status_code == 400
        exchanged = client.post("/token", data=data)
        assert exchanged.status_code == 200, exchanged.text
        token = exchanged.json()["access_token"]
        assert client.post("/token", data=data).status_code == 400
        assert client.post("/oauth/consent", data={"ticket": ticket, "key": owner}).status_code == 422
        headers = {"Authorization": "Bearer " + token, "Accept": "application/json, text/event-stream"}
        result = client.post(
            "/mcp", headers=headers, json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
        )
        assert result.status_code == 200, result.text
        assert "render_video" in [t["name"] for t in result.json()["result"]["tools"]]
        assert client.get("/api/projects", headers=headers).status_code == 200
        refreshed = client.post(
            "/token",
            data={
                "grant_type": "refresh_token",
                "client_id": client_id,
                "refresh_token": exchanged.json()["refresh_token"],
            },
        )
        assert refreshed.status_code == 200, refreshed.text
        assert client.get("/api/projects", headers=headers).status_code == 401
        assert (
            client.post(
                "/token",
                data={
                    "grant_type": "refresh_token",
                    "client_id": client_id,
                    "refresh_token": exchanged.json()["refresh_token"],
                },
            ).status_code
            == 400
        )
        token = refreshed.json()["access_token"]
        headers["Authorization"] = "Bearer " + token
        assert client.get("/api/projects", headers=headers).status_code == 200
        assert client.post("/revoke", data={"token": token, "client_id": client_id}).status_code == 200
        assert client.get("/api/projects", headers=headers).status_code == 401
        assert (
            client.post(
                "/token",
                data={
                    "grant_type": "refresh_token",
                    "client_id": client_id,
                    "refresh_token": refreshed.json()["refresh_token"],
                },
            ).status_code
            == 400
        )
