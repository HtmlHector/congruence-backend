"""Host-native agent authentication.

Implements Host Custody (``prd.md`` Invariant 4, ``trd.md`` section 7): the
user authenticates a CLI harness by running the *vendor's own* login on the
host. Congruence relays PTY bytes and surfaces a temporary URL. It never
receives, stores, decrypts, or proxies a credential.

Why the callback bridge exists
------------------------------
The host is a remote Fly Sprite microVM, not the user's laptop. ``claude login``
completes OAuth against a loopback callback bound on the machine running the
CLI, so the user's browser cannot reach it directly. We publish that port
through the existing private-service-address machinery as a single-use,
short-lived URL. The user's browser then talks to the *vendor*; only the
vendor's redirect comes back to us, and the CLI itself performs the code
exchange and writes the token to ``$HOME``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

# Loopback ports the supported CLIs bind their OAuth callbacks to. Kept as an
# explicit allowlist so a compromised or unexpected process on the host cannot
# get a public service URL just by listening.
CALLBACK_PORTS: dict[str, int] = {
    "claude": 1455,
    "codex": 1455,
}

LOGIN_COMMANDS: dict[str, list[str]] = {
    "claude": ["claude", "login"],
    "codex": ["codex", "login"],
}

SUPPORTED_HARNESSES = tuple(LOGIN_COMMANDS)

# Credential presence on the host. We check existence only, never contents.
CREDENTIAL_PATHS: dict[str, str] = {
    "claude": "~/.claude/.credentials.json",
    "codex": "~/.codex/auth.json",
}

# Where the CLI prints its authorize URL. Matched only so we can surface a
# tappable link; the URL is passed through to the client unmodified.
_AUTHORIZE_URL_RE = re.compile(r"https://[^\s\"'<>\]]+")

MAX_LOGIN_SECONDS = 600
CALLBACK_URL_TTL_SECONDS = 600


class HarnessLoginError(Exception):
    """Raised when a host-native login cannot be started or completed."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass
class HarnessLoginSession:
    """In-flight login handshake for one lane.

    Holds no credential. ``authorize_url`` is a pointer at a port on the user's
    own host; ``device_code`` is a short human-typable string the vendor
    intends the user to enter. Neither is a secret we minted or hold.
    """

    lane_id: str
    harness: str
    state: str = "awaiting_user"
    authorize_url: str | None = None
    device_code: str | None = None
    callback_port: int | None = None
    callback_service_id: str | None = None
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    expires_at: datetime = field(
        default_factory=lambda: datetime.now(UTC)
        + timedelta(seconds=CALLBACK_URL_TTL_SECONDS)
    )
    error_code: str | None = None
    error_message: str | None = None

    def is_expired(self, now: datetime | None = None) -> bool:
        return (now or datetime.now(UTC)) >= self.expires_at

    def seconds_remaining(self, now: datetime | None = None) -> int:
        delta = self.expires_at - (now or datetime.now(UTC))
        return max(0, int(delta.total_seconds()))

    def to_public_dict(self) -> dict[str, object]:
        """Shape returned to the browser.

        ``authorize_url`` is included only while the handshake is genuinely
        pending. Once connected or expired it is omitted so a stale link cannot
        be replayed from a logged history entry.
        """
        pending = self.state == "awaiting_user" and not self.is_expired()
        return {
            "harness": self.harness,
            "state": self.state,
            "authorize_url": self.authorize_url if pending else None,
            "device_code": self.device_code if pending else None,
            "expires_at": self.expires_at.isoformat(),
            "seconds_remaining": self.seconds_remaining() if pending else 0,
            "error_code": self.error_code,
            "error_message": self.error_message,
        }


def build_login_command(harness: str) -> list[str]:
    if harness not in LOGIN_COMMANDS:
        raise HarnessLoginError(
            "unsupported_harness",
            f"No host login flow is defined for '{harness}'.",
        )
    return list(LOGIN_COMMANDS[harness])


def extract_authorize_url(output: str) -> str | None:
    """Pull the vendor authorize URL out of CLI output, if present."""
    match = _AUTHORIZE_URL_RE.search(output or "")
    return match.group(0) if match else None


def credential_path(harness: str) -> str | None:
    return CREDENTIAL_PATHS.get(harness)


class LoginSessionRegistry:
    """Tracks in-flight logins per lane.

    Deliberately in-process and unpersisted. A login is a short-lived,
    user-initiated interaction; surviving a control-plane restart is not worth
    the complexity, and the host's ``$HOME`` remains the durable source of
    truth regardless.
    """

    def __init__(self) -> None:
        self._sessions: dict[str, HarnessLoginSession] = {}

    def start(self, lane_id: str, harness: str) -> HarnessLoginSession:
        build_login_command(harness)  # validates harness
        session = HarnessLoginSession(lane_id=lane_id, harness=harness)
        self._sessions[lane_id] = session
        return session

    def get(self, lane_id: str) -> HarnessLoginSession | None:
        session = self._sessions.get(lane_id)
        if session and session.is_expired() and session.state == "awaiting_user":
            session.state = "error"
            session.error_code = "expired"
            session.error_message = "This sign-in link expired. Generate a new one."
            session.authorize_url = None
        return session

    def attach_callback(
        self, lane_id: str, port: int, service_id: str, authorize_url: str | None
    ) -> HarnessLoginSession:
        session = self._require(lane_id)
        session.callback_port = port
        session.callback_service_id = service_id
        session.authorize_url = authorize_url
        return session

    def complete(self, lane_id: str) -> HarnessLoginSession:
        session = self._require(lane_id)
        session.state = "connected"
        session.authorize_url = None
        session.device_code = None
        return session

    def fail(self, lane_id: str, code: str, message: str) -> HarnessLoginSession:
        session = self._require(lane_id)
        session.state = "error"
        session.error_code = code
        session.error_message = message
        session.authorize_url = None
        return session

    def clear(self, lane_id: str) -> None:
        self._sessions.pop(lane_id, None)

    def _require(self, lane_id: str) -> HarnessLoginSession:
        session = self._sessions.get(lane_id)
        if session is None:
            raise HarnessLoginError(
                "no_login_in_progress",
                "No sign-in is in progress for this lane.",
            )
        return session


registry = LoginSessionRegistry()
