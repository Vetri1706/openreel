"""Single-owner OAuth authorization using the MCP SDK's protocol handlers.

PKCE, client authentication, redirect validation, and protocol errors are handled
by the SDK. Owner consent is explicit; access tokens expire after one hour.
"""

import hashlib
import secrets
import time
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    AuthorizeError,
    RefreshToken,
    TokenError,
)
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken

from .store import Store


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


class StudioRefreshToken(RefreshToken):
    family: str
    access_key: str


class OwnerOAuth:
    def __init__(self, store: Store, public_url: str):
        self.store = store
        self.url = public_url.rstrip("/")
        self.resource = self.url + "/mcp"

    async def get_client(self, client_id):
        data = self.store.auth_get("client", client_id)
        return OAuthClientInformationFull.model_validate(data) if data else None

    async def register_client(self, client_info):
        self.store.auth_put(
            "client", client_info.client_id, client_info.model_dump(mode="json"), time.time() + 30 * 86400
        )

    async def authorize(self, client, params: AuthorizationParams):
        if params.resource and params.resource != self.resource:
            raise AuthorizeError("invalid_target", "Resource must be this server's /mcp URL.")
        ticket = secrets.token_urlsafe(32)
        self.store.auth_put(
            "consent",
            digest(ticket),
            dict(
                client_id=client.client_id,
                client_name=client.client_name or "MCP client",
                params=params.model_dump(mode="json"),
            ),
            time.time() + 600,
        )
        return self.url + "/oauth/consent?" + urlencode({"ticket": ticket})

    def approve(self, ticket: str) -> str:
        flow = self.store.auth_get("consent", digest(ticket), pop=True)
        if not flow:
            raise ValueError(
                "This connection request expired or was already used. Start again in your AI client."
            )
        params = AuthorizationParams.model_validate(flow["params"])
        code = secrets.token_urlsafe(32)
        value = AuthorizationCode(
            code=code,
            client_id=flow["client_id"],
            scopes=params.scopes or ["studio"],
            expires_at=time.time() + 120,
            code_challenge=params.code_challenge,
            redirect_uri=params.redirect_uri,
            redirect_uri_provided_explicitly=params.redirect_uri_provided_explicitly,
            resource=self.resource,
            subject="owner",
        ).model_dump(mode="json")
        value.pop("code")
        self.store.auth_put("code", digest(code), value, time.time() + 120)
        url = urlsplit(str(params.redirect_uri))
        query = parse_qsl(url.query) + [("code", code)]
        if params.state is not None:
            query.append(("state", params.state))
        return urlunsplit((url.scheme, url.netloc, url.path, urlencode(query), url.fragment))

    async def load_authorization_code(self, client, authorization_code):
        data = self.store.auth_get("code", digest(authorization_code))
        if not data or data["client_id"] != client.client_id:
            return None
        return AuthorizationCode(code=authorization_code, **data)

    async def exchange_authorization_code(self, client, authorization_code):
        data = self.store.auth_get("code", digest(authorization_code.code), pop=True)
        if not data or data["client_id"] != client.client_id:
            raise TokenError("invalid_grant", "Authorization code expired or already used.")
        return self._issue(client.client_id, data["scopes"], secrets.token_urlsafe(24))

    def _issue(self, client_id: str, scopes: list[str], family: str):
        token = secrets.token_urlsafe(32)
        refresh = secrets.token_urlsafe(32)
        value = dict(
            client_id=client_id,
            scopes=scopes,
            expires_at=int(time.time() + 3600),
            resource=self.resource,
            subject="owner",
            claims={"family": family},
        )
        self.store.auth_put("token", digest(token), value, time.time() + 3600)
        self.store.auth_put(
            "refresh",
            digest(refresh),
            dict(
                client_id=client_id,
                scopes=scopes,
                expires_at=int(time.time() + 30 * 86400),
                resource=self.resource,
                subject="owner",
                family=family,
                access_key=digest(token),
            ),
            time.time() + 30 * 86400,
        )
        return OAuthToken(access_token=token, refresh_token=refresh, expires_in=3600, scope=" ".join(scopes))

    async def load_access_token(self, token):
        data = self.store.auth_get("token", digest(token))
        if not data or self.store.auth_get("revoked", data["claims"]["family"]):
            return None
        return AccessToken(token=token, **data)

    async def load_refresh_token(self, client, refresh_token):
        data = self.store.auth_get("refresh", digest(refresh_token))
        if (
            not data
            or data["client_id"] != client.client_id
            or self.store.auth_get("revoked", data["family"])
        ):
            return None
        return StudioRefreshToken(token=refresh_token, **data)

    async def exchange_refresh_token(self, client, refresh_token, scopes):
        data = self.store.auth_get("refresh", digest(refresh_token.token), pop=True)
        if (
            not data
            or data["client_id"] != client.client_id
            or self.store.auth_get("revoked", data["family"])
        ):
            raise TokenError("invalid_grant", "Refresh token expired or already used. Reconnect.")
        if not set(scopes).issubset(data["scopes"]):
            raise TokenError("invalid_scope", "Cannot expand token permissions.")
        self.store.auth_get("token", data["access_key"], pop=True)
        return self._issue(client.client_id, scopes, data["family"])

    async def revoke_token(self, token):
        family = token.family if isinstance(token, StudioRefreshToken) else token.claims["family"]
        self.store.auth_put("revoked", family, {"revoked": True}, time.time() + 31 * 86400)
        self.store.auth_get("token", digest(token.token), pop=True)
        self.store.auth_get("refresh", digest(token.token), pop=True)
