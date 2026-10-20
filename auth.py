"""Google Workspace authentication for the hosted MCP endpoint."""

import asyncio
from typing import Any

from fastmcp.server.auth import TokenVerifier
from fastmcp.server.auth.auth import AccessToken as FastMCPAccessToken
from fastmcp.server.auth.providers.google import GoogleProvider
from google.auth.exceptions import GoogleAuthError
from google.auth.transport.requests import Request as GoogleRequest
from google.oauth2.id_token import verify_oauth2_token
from mcp.server.auth.provider import AccessToken, TokenError


class DenyAllVerifier(TokenVerifier):
    """Keep HTTP closed when OAuth has not been configured."""

    async def verify_token(self, token: str) -> FastMCPAccessToken | None:
        _ = token
        return None


class WorkspaceGoogleProvider(GoogleProvider):
    """Allow only verified users in one Google Workspace domain."""

    def __init__(
        self,
        *,
        client_id: str,
        client_secret: str,
        base_url: str,
        domain: str,
    ) -> None:
        self._workspace_domain = domain.lower().removeprefix("@")
        self._google_client_id = client_id
        super().__init__(
            client_id=client_id,
            client_secret=client_secret,
            base_url=base_url,
            required_scopes=["openid", "email"],
            extra_authorize_params={"hd": self._workspace_domain},
        )

    async def _extract_upstream_claims(
        self, idp_tokens: dict[str, Any]
    ) -> dict[str, Any]:
        """Verify Google's signed ID token before issuing an MCP token."""
        id_token = idp_tokens.get("id_token")
        if not isinstance(id_token, str) or not id_token:
            raise TokenError("invalid_grant", "Google ID token is required")

        try:
            claims = await asyncio.to_thread(
                verify_oauth2_token,
                id_token,
                GoogleRequest(),
                self._google_client_id,
            )
        except (GoogleAuthError, ValueError) as exc:
            raise TokenError("invalid_grant", "Google ID token is invalid") from exc

        domain = claims.get("hd")
        email = claims.get("email")
        if (
            not isinstance(domain, str)
            or domain.lower() != self._workspace_domain
            or not isinstance(email, str)
            or email.rpartition("@")[2].lower() != self._workspace_domain
            or claims.get("email_verified") is not True
        ):
            raise TokenError("invalid_grant", "Google Workspace account is not allowed")

        return {
            "sub": claims["sub"],
            "hd": self._workspace_domain,
            "email": email,
            "email_verified": True,
        }

    async def load_access_token(self, token: str) -> AccessToken | None:
        """Reject MCP tokens without the verified Workspace identity."""
        access_token = await super().load_access_token(token)
        if access_token is None:
            return None

        claims = (access_token.claims or {}).get("upstream_claims")
        if not isinstance(claims, dict):
            return None
        if claims.get("hd") != self._workspace_domain:
            return None
        if claims.get("email_verified") is not True:
            return None
        email = claims.get("email")
        if not isinstance(email, str):
            return None
        if email.rpartition("@")[2].lower() != self._workspace_domain:
            return None
        return access_token
