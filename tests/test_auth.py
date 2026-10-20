"""Security checks for Google Workspace access to the HTTP server."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
from fastmcp import settings
from fastmcp.server.auth.providers.google import GoogleProvider
from mcp.server.auth.provider import AccessToken, TokenError

from auth import WorkspaceGoogleProvider
from server import mcp


class WorkspaceAuthTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        home_patch = patch.object(settings, "home", Path(self.temp_dir.name))
        home_patch.start()
        self.addCleanup(home_patch.stop)
        self.provider = WorkspaceGoogleProvider(
            client_id="test.apps.googleusercontent.com",
            client_secret="test-secret",
            base_url="https://example.onrender.com",
            domain="example.com",
        )

    async def test_verified_workspace_identity_is_accepted(self) -> None:
        claims = {
            "sub": "google-user-id",
            "hd": "example.com",
            "email": "person@example.com",
            "email_verified": True,
        }
        with patch("auth.verify_oauth2_token", return_value=claims) as verify:
            result = await self.provider._extract_upstream_claims(
                {"id_token": "signed-token"}
            )
        self.assertEqual(result, claims)
        verify.assert_called_once()

    async def test_non_workspace_identities_are_rejected(self) -> None:
        valid = {
            "sub": "google-user-id",
            "hd": "example.com",
            "email": "person@example.com",
            "email_verified": True,
        }
        for changes in (
            {"hd": None},
            {"hd": "other.com"},
            {"email": "person@other.com"},
            {"email_verified": False},
        ):
            with (
                self.subTest(changes=changes),
                patch("auth.verify_oauth2_token", return_value=valid | changes),
                self.assertRaises(TokenError),
            ):
                await self.provider._extract_upstream_claims(
                    {"id_token": "signed-token"}
                )

        with self.assertRaises(TokenError):
            await self.provider._extract_upstream_claims({})

    async def test_issued_token_still_requires_workspace_claims(self) -> None:
        token = AccessToken(
            token="mcp-token",
            client_id="client",
            scopes=["openid", "email"],
            claims={
                "upstream_claims": {
                    "hd": "example.com",
                    "email": "person@example.com",
                    "email_verified": True,
                }
            },
        )
        with patch.object(
            GoogleProvider, "load_access_token", new_callable=AsyncMock
        ) as load:
            load.return_value = token
            self.assertIs(await self.provider.load_access_token("mcp-token"), token)
            token.claims = {
                "upstream_claims": {
                    "hd": "other.com",
                    "email": "person@example.com",
                    "email_verified": True,
                }
            }
            self.assertIsNone(await self.provider.load_access_token("mcp-token"))


class HttpBoundaryTests(unittest.IsolatedAsyncioTestCase):
    async def test_health_is_public_but_mcp_requires_auth(self) -> None:
        transport = httpx.ASGITransport(app=mcp.http_app())
        async with httpx.AsyncClient(
            transport=transport, base_url="http://localhost"
        ) as client:
            health = await client.get("/healthz")
            mcp_response = await client.get("/mcp")
        self.assertEqual(health.status_code, 200)
        self.assertEqual(health.json(), {"status": "ok"})
        self.assertEqual(mcp_response.status_code, 401)


if __name__ == "__main__":
    unittest.main()
