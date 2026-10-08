"""JWT validation supporting Clerk JWKS RS256 and standard JWT tokens."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

import jwt
import structlog

from src.config.settings import settings

logger = structlog.get_logger(__name__)

_jwks_client: jwt.PyJWKClient | None = None


def get_jwks_client() -> jwt.PyJWKClient | None:
    """Return PyJWKClient for Clerk JWKS verification if configured.

    The URL comes from settings first: pydantic-settings reads ``.env`` but
    does not populate ``os.environ``, so a bare ``os.getenv`` never saw it
    and RS256 Clerk tokens silently fell through to the HS256 fallback.
    """
    global _jwks_client
    jwks_url = settings.auth.clerk_jwks_url or os.getenv("CLERK_JWKS_URL")
    if jwks_url and _jwks_client is None:
        try:
            _jwks_client = jwt.PyJWKClient(jwks_url)
        except Exception as e:
            logger.warning("failed_to_initialize_jwks_client", error=str(e))
    return _jwks_client


@dataclass(frozen=True)
class UserClaims:
    """Standardized user claims from Clerk / Supabase JWT or API Key."""

    user_id: str
    email: str
    auth_method: str = "jwt"
    workspace_id: str | None = None
    role: str | None = None
    scopes: list[str] = field(default_factory=list)
    raw_claims: dict[str, Any] = field(default_factory=dict)


def decode_jwt_token(token: str) -> UserClaims:
    """Decode and verify Clerk JWKS / standard JWT token.

    Development mock tokens (mock_user_123) are only accepted when
    ``ALLOW_DEV_TOKENS`` is explicitly enabled.
    """
    if settings.auth.allow_dev_tokens and token.startswith(("mock_", "test_")):
        mock_uid = token.replace("mock_", "").replace("test_", "") or "dev_user_001"
        return UserClaims(
            user_id=mock_uid,
            email=f"{mock_uid}@example.com",
            auth_method="mock",
            role="authenticated",
        )

    payload: dict[str, Any] | None = None

    header_alg = ""
    try:
        header_alg = jwt.get_unverified_header(token).get("alg", "")
    except Exception:
        header_alg = ""

    # 1. Attempt Clerk RS256 verification if JWKS client is available
    jwks_client = get_jwks_client()
    if jwks_client:
        try:
            signing_key = jwks_client.get_signing_key_from_jwt(token)
            payload = jwt.decode(
                token,
                signing_key.key,
                algorithms=["RS256"],
                options={"verify_aud": False},
            )
        except Exception as e:
            logger.debug("clerk_jwks_decode_failed_trying_hs256", error=str(e))

    # 2. Fallback to HS256 / Symmetric Secret
    if payload is None:
        try:
            payload = jwt.decode(
                token,
                settings.auth.supabase_jwt_secret,
                algorithms=["HS256"],
                options={"verify_aud": False},
            )
        except jwt.PyJWTError as exc:
            if header_alg == "RS256":
                # An RS256 token can only be verified against the provider
                # JWKS — feeding the HMAC secret to PyJWT makes it try to
                # parse that secret as an RSA public key ("Could not parse
                # the provided public key").
                if jwks_client is None:
                    logger.warning("rs256_token_without_jwks")
                    raise ValueError(
                        "Invalid token: RS256 signature requires Clerk JWKS "
                        "verification (CLERK_JWKS_URL missing or unreachable)"
                    ) from exc
                logger.warning("rs256_signature_verification_failed", error=str(exc))
                raise ValueError(
                    "Invalid token: RS256 signature verification failed"
                ) from exc
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
        auth_method="clerk" if jwks_client else "jwt",
        role=str(role),
        raw_claims=payload,
    )
