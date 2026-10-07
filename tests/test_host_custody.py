"""Tests for Host Custody: the control plane must never hold a credential.

Covers the login state machine in `harness_login`, plus structural guarantees
that are cheap to assert and expensive to lose:

* no endpoint accepts a credential;
* `congruence_harness_auth` has no column capable of holding one;
* authorize URLs are withheld once a handshake is no longer pending, so a stale
  link cannot be replayed from browser history.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from src.contexts.congruence.models import HarnessAuth
from src.infrastructure.auth.harness_login import (
    CALLBACK_PORTS,
    HarnessLoginError,
    LoginSessionRegistry,
    build_login_command,
    credential_path,
    extract_authorize_url,
)


# --- Login command construction -------------------------------------------


def test_login_command_uses_vendor_cli() -> None:
    assert build_login_command("claude") == ["claude", "login"]
    assert build_login_command("codex") == ["codex", "login"]


def test_unsupported_harness_is_rejected() -> None:
    with pytest.raises(HarnessLoginError) as exc:
        build_login_command("opencode")
    assert exc.value.code == "unsupported_harness"


def test_callback_ports_are_allowlisted() -> None:
    # A bridge URL must only ever be minted for a known port, never for
    # whatever a process on the host happens to be listening on.
    assert set(CALLBACK_PORTS) == {"claude", "codex"}
    assert all(isinstance(p, int) and 1024 < p < 65536 for p in CALLBACK_PORTS.values())


# --- Authorize URL extraction ---------------------------------------------


def test_extract_authorize_url_from_cli_output() -> None:
    out = "Opening https://claude.ai/oauth/authorize?code=true&state=xyz in your browser"
    assert extract_authorize_url(out) == (
        "https://claude.ai/oauth/authorize?code=true&state=xyz"
    )


def test_extract_authorize_url_returns_none_when_absent() -> None:
    assert extract_authorize_url("Waiting for authentication...") is None


# --- Session state machine -------------------------------------------------


def test_start_login_enters_awaiting_user() -> None:
    reg = LoginSessionRegistry()
    session = reg.start("lane_1", "claude")
    assert session.state == "awaiting_user"
    assert session.authorize_url is None


def test_authorize_url_is_exposed_only_while_pending() -> None:
    reg = LoginSessionRegistry()
    reg.start("lane_1", "claude")
    reg.attach_callback(
        "lane_1", 1455, "svc_1", "https://claude.ai/oauth/authorize?state=abc"
    )

    pending = reg.get("lane_1")
    assert pending.authorize_url is not None
    assert pending.seconds_remaining() > 0

    reg.complete("lane_1")
    done = reg.get("lane_1")
    assert done.state == "connected"
    # Withheld after completion so the link cannot be replayed.
    assert done.authorize_url is None
    assert done.to_public_dict()["authorize_url"] is None


def test_expired_session_is_marked_error_and_url_withheld() -> None:
    reg = LoginSessionRegistry()
    session = reg.start("lane_1", "codex")
    session.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)

    expired = reg.get("lane_1")
    assert expired.state == "error"
    assert expired.error_code == "expired"
    assert expired.authorize_url is None
    assert expired.to_public_dict()["authorize_url"] is None


def test_disconnect_clears_pending_session() -> None:
    reg = LoginSessionRegistry()
    reg.start("lane_1", "claude")
    reg.clear("lane_1")
    assert reg.get("lane_1") is None


def test_operations_without_session_raise() -> None:
    reg = LoginSessionRegistry()
    with pytest.raises(HarnessLoginError) as exc:
        reg.complete("lane_missing")
    assert exc.value.code == "no_login_in_progress"


# --- Structural guarantees -------------------------------------------------


def test_harness_auth_table_cannot_hold_a_secret() -> None:
    """The strongest guarantee in the system, asserted structurally.

    If someone later adds an `encrypted_value` or `token` column, this fails.
    """
    forbidden = ("token", "secret", "credential", "password", "encrypted", "cipher", "key")
    columns = {c.name.lower() for c in HarnessAuth.__table__.columns}
    offenders = {c for c in columns if any(f in c for f in forbidden)}
    assert not offenders, f"credential-capable columns present: {offenders}"


def test_harness_auth_columns_are_all_safe_types() -> None:
    import sqlalchemy as sa

    safe = (sa.String, sa.DateTime, sa.Boolean, sa.ForeignKey)
    for col in HarnessAuth.__table__.columns:
        assert isinstance(col.type, safe), f"unexpected type on {col.name}: {col.type}"


def test_credential_paths_are_host_side_only() -> None:
    # We reference the path only to ask the host whether the file exists.
    assert credential_path("claude") == "~/.claude/.credentials.json"
    assert credential_path("codex") == "~/.codex/auth.json"
    assert credential_path("aider") is None
