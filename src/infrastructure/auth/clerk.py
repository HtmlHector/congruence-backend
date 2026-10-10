"""Clerk authentication primitive — JWT verification, JIT provisioning, and Svix webhooks."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import httpx
import jwt
import structlog
from jwt import PyJWKClient
from svix.webhooks import Webhook

from src.config.settings import settings

logger = structlog.get_logger(__name__)


@dataclass(frozen=True)
class ClerkClaims:
    """Verified Clerk authentication claims."""

    user_id: str
    org_id: str | None = None
    session_id: str | None = None
    email: str | None = None
    raw_claims: dict[str, Any] = field(default_factory=dict)


class ClerkService:
    """Clerk REST API and JWT verification service."""

    def __init__(
        self,
        secret_key: str | None = None,
        jwks_url: str | None = None,
        webhook_secret: str | None = None,
    ) -> None:
        self._secret_key = secret_key or settings.clerk.secret_key
        self._jwks_url = jwks_url or settings.clerk.jwks_url
        self._webhook_secret = webhook_secret or settings.clerk.webhook_secret
        self._http = httpx.AsyncClient(timeout=httpx.Timeout(10.0))
        self._jwk_client: PyJWKClient | None = None

        if self._jwks_url:
            self._jwk_client = PyJWKClient(self._jwks_url, cache_jwk_set=True, lifespan=3600)

    async def close(self) -> None:
        """Close HTTP client."""
        await self._http.aclose()

    async def verify_token(self, token: str) -> ClerkClaims:
        """Verify Clerk JWT and extract subject and org claims."""
        # Development mode bypass for ease of testing with placeholder keys
        if settings.app.environment == "development" and self._secret_key.startswith(
            "sk_test_placeholder"
        ):
            try:
                # In local dev without live keys, decode without signature verification
                unverified = jwt.decode(token, options={"verify_signature": False})
                return ClerkClaims(
                    user_id=unverified.get("sub", "dev_user_123"),
                    org_id=unverified.get("org_id"),
                    session_id=unverified.get("sid"),
                    email=unverified.get("email"),
                    raw_claims=unverified,
                )
            except Exception:
                # Fallback to dev user
                return ClerkClaims(
                    user_id="dev_user_123",
                    org_id="00000000-0000-0000-0000-000000000001",
                    session_id="dev_session_123",
                    email="dev@parabox.so",
                )

        if not self._jwk_client:
            raise RuntimeError("JWKS URL is not configured for Clerk token verification")

        signing_key = self._jwk_client.get_signing_key_from_jwt(token)
        payload = jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            options={"verify_aud": False},
            leeway=60,
        )

        return ClerkClaims(
            user_id=payload["sub"],
            org_id=payload.get("org_id"),
            session_id=payload.get("sid"),
            email=payload.get("email"),
            raw_claims=payload,
        )

    async def get_user(self, clerk_user_id: str) -> dict[str, Any]:
        """Fetch user profile from Clerk API for JIT provisioning."""
        if self._secret_key.startswith("sk_test_placeholder"):
            return {
                "id": clerk_user_id,
                "first_name": "Dev",
                "last_name": "User",
                "email_addresses": [{"email_address": "dev@parabox.so"}],
                "image_url": "https://avatar.vercel.sh/parabox",
            }

        response = await self._http.get(
            f"https://api.clerk.com/v1/users/{clerk_user_id}",
            headers={"Authorization": f"Bearer {self._secret_key}"},
        )
        response.raise_for_status()
        from typing import cast

        return cast(dict[str, Any], response.json())

    def verify_webhook(self, headers: dict[str, str], payload: bytes) -> dict[str, Any]:
        """Verify Clerk webhook payload signature using Svix."""
        import json
        from typing import cast

        if (
            self._webhook_secret.startswith("whsec_placeholder")
            and settings.app.environment == "development"
        ):
            return cast(dict[str, Any], json.loads(payload.decode("utf-8")))

        wh = Webhook(self._webhook_secret)
        wh.verify(payload, headers)
        return cast(dict[str, Any], json.loads(payload.decode("utf-8")))


clerk_service = ClerkService()
