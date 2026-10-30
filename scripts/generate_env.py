"""Generate .env from .env.example with local, random secrets.

Safe to run repeatedly: an existing .env is never overwritten. External
provider credentials (Clerk, Stripe, Fly, OAuth apps) stay as placeholders and
must be filled in by hand.
"""

from __future__ import annotations

import re
import secrets
from pathlib import Path

from cryptography.fernet import Fernet

ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = ROOT / ".env.example"
TARGET = ROOT / ".env"

GENERATED_KEYS = (
    "ENCRYPTION_KEY",
    "INTEGRATIONS_STATE_SIGNING_KEY",
    "DEVBOX_CAPTURE_TOKEN_SECRET",
    "GATEWAY_TICKET_SECRET",
    "PREVIEW_COOKIE_SECRET",
)


def generate_value(key: str) -> str:
    """Return a fresh secret for a key."""
    if key == "ENCRYPTION_KEY":
        return Fernet.generate_key().decode()
    return secrets.token_hex(32)


def main() -> None:
    if TARGET.exists():
        print(f"{TARGET.name} already exists; leaving it untouched.")
        return

    if not TEMPLATE.exists():
        raise SystemExit(f"Missing template: {TEMPLATE}")

    content = TEMPLATE.read_text(encoding="utf-8")
    for key in GENERATED_KEYS:
        content, count = re.subn(
            rf"(?m)^{key}=.*$", f"{key}={generate_value(key)}", content
        )
        if count != 1:
            raise SystemExit(f"Expected exactly one {key} line, found {count}")

    TARGET.write_text(content, encoding="utf-8")
    TARGET.chmod(0o600)
    print(f"Wrote {TARGET.name} with generated: {', '.join(GENERATED_KEYS)}")
    print("Fill in Clerk, Stripe, Fly, and OAuth credentials before deploying.")


if __name__ == "__main__":
    main()
