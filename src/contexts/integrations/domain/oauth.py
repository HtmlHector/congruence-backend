"""Cryptographically signed OAuth state generation and validation."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from typing import Any
from uuid import UUID

from src.config.settings import settings
from src.contexts.integrations.domain.entities import IntegrationProvider
from src.security.hashing import generate_secure_token, verify_hmac


def generate_oauth_state(
    workspace_id: UUID,
    user_id: str,
    provider: IntegrationProvider,
) -> str:
    """Create a tamper-evident signed state string encoding workspace and user context."""
    data: dict[str, Any] = {
        "ws": str(workspace_id),
        "uid": user_id,
        "prv": provider.value,
        "ts": int(time.time()),
        "nonce": generate_secure_token(16),
    }
    payload_str = json.dumps(data, separators=(",", ":"))
    payload_b64 = base64.urlsafe_b64encode(payload_str.encode("utf-8")).decode("utf-8").rstrip("=")

    secret = settings.integrations.state_signing_key.encode("utf-8")
    signature = hmac.new(secret, payload_b64.encode("utf-8"), hashlib.sha256).hexdigest()

    return f"{payload_b64}.{signature}"


def verify_oauth_state(state_token: str, max_age_seconds: int = 900) -> dict[str, Any]:
    """Verify state token signature and validity, returning decoded context."""
    parts = state_token.split(".")
    if len(parts) != 2:
        raise ValueError("Invalid state format")

    payload_b64, signature = parts
    secret = settings.integrations.state_signing_key.encode("utf-8")

    if not verify_hmac(secret, payload_b64.encode("utf-8"), signature):
        raise ValueError("Invalid state signature")

    from typing import cast

    # Re-pad base64
    padded = payload_b64 + "=" * (-len(payload_b64) % 4)
    data = cast(
        dict[str, Any], json.loads(base64.urlsafe_b64decode(padded.encode("utf-8")).decode("utf-8"))
    )

    issued_at = data.get("ts", 0)
    if time.time() - issued_at > max_age_seconds:
        raise ValueError("OAuth state token has expired")

    return data
