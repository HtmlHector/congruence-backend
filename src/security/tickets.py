"""Short-lived signed tickets for terminal attach and credential capture.

Tickets are HMAC-SHA256 signed, URL-safe, and carry a mandatory expiry. They are
never stored server-side; callers must bind every meaningful claim (terminal id,
user id, workspace id) into the payload so a leaked ticket cannot be replayed
against a different resource.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

TICKET_VERSION = 1


class TicketError(Exception):
    """Raised when a ticket is malformed, tampered with, or expired."""


@dataclass(frozen=True)
class VerifiedTicket:
    """Decoded ticket claims."""

    claims: dict[str, Any]
    expires_at: int


def _b64encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("utf-8").rstrip("=")


def _b64decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)


def _signature(secret: str, body: bytes) -> str:
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).digest()
    return _b64encode(digest)


def sign_ticket(payload: dict[str, Any], *, secret: str, ttl_seconds: int) -> str:
    """Return a signed ticket containing the payload plus version and expiry claims."""
    if ttl_seconds <= 0:
        raise ValueError("ttl_seconds must be positive")

    claims = {
        **payload,
        "v": TICKET_VERSION,
        "exp": int(time.time()) + ttl_seconds,
        "jti": uuid4().hex,
    }
    body = json.dumps(claims, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return f"{_b64encode(body)}.{_signature(secret, body)}"


def verify_ticket(token: str, *, secret: str) -> VerifiedTicket:
    """Validate a ticket's signature and expiry, returning its claims."""
    try:
        encoded_body, encoded_signature = token.split(".", 1)
        body = _b64decode(encoded_body)
    except (ValueError, TypeError) as exc:
        raise TicketError("Malformed ticket") from exc

    expected = _signature(secret, body)
    if not hmac.compare_digest(expected, encoded_signature):
        raise TicketError("Invalid ticket signature")

    try:
        claims = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise TicketError("Malformed ticket payload") from exc

    if not isinstance(claims, dict) or claims.get("v") != TICKET_VERSION:
        raise TicketError("Unsupported ticket version")

    expires_at = claims.get("exp")
    if not isinstance(expires_at, int) or expires_at < int(time.time()):
        raise TicketError("Ticket expired")

    return VerifiedTicket(claims=claims, expires_at=expires_at)
