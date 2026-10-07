"""JWT validation and UserClaims models."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import jwt
import structlog

from src.config.settings import settings

logger = structlog.get_logger(__name__)


@dataclass(frozen=True)
class UserClaims:
    """Standardized user claims from Supabase JWT or API Key."""

    user_id: str
    email: str
    auth_method: str = "jwt"
    workspace_id: str | None = None
    role: str | None = None
    scopes: list[str] = field(default_factory=list)
    raw_claims: dict[str, Any] = field(default_factory=dict)


def decode_jwt_token(token: str) -> UserClaims:
    """Decode and verify Supabase / standard JWT token.

    Supports development mock tokens (mock_user_123) when DEBUG=true.
    """
    if settings.debug and token.startswith(("mock_", "test_")):
        mock_uid = token.replace("mock_", "").replace("test_", "") or "dev_user_001"
        return UserClaims(
            user_id=mock_uid,
            email=f"{mock_uid}@example.com",
            auth_method="mock",
            role="authenticated",
        )

    try:
        # Supabase JWT default algorithm is HS256 with JWT secret
        payload = jwt.decode(
            token,
            settings.auth.supabase_jwt_secret,
            algorithms=["HS256", "RS256"],
            options={"verify_aud": False},
        )
    except jwt.PyJWTError as exc:
        logger.warning("jwt_verification_failed", error=str(exc))
        raise ValueError(f"Invalid token: {exc}") from exc

    user_id = payload.get("sub") or payload.get("user_id")
    if not user_id:
        raise ValueError("Token missing user identifier ('sub' or 'user_id')")

    email = payload.get("email") or payload.get("user_metadata", {}).get(
        "email", "unknown@example.com"
    )
    role = payload.get("role") or payload.get("app_metadata", {}).get("role", "authenticated")

    return UserClaims(
        user_id=str(user_id),
        email=str(email),
        auth_method="jwt",
        role=str(role),
        raw_claims=payload,
    )
