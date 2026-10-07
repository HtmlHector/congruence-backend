"""Agent connection endpoints: GitHub App plus host-native harness login.

There is deliberately no endpoint here that accepts, stores, or returns a
credential. The pre-POC ``/vault/keys`` routes were removed under Host Custody
(``trd.md`` section 7.4): they wrote AES-256-GCM ciphertext into
``congruence_vault_secrets`` with no tenant scoping and an unauthenticated
route, and the key silently defaulted to 32 zero bytes.

Connection state is *reported by the host* (does ``$HOME`` contain the
vendor's credential file?) rather than known by us.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.config.settings import settings
from src.contexts.congruence.models import HarnessAuth, Host, Lane, Project, GitHubAccount
from src.infrastructure.auth.harness_login import (
    SUPPORTED_HARNESSES,
    HarnessLoginError,
    build_login_command,
    credential_path,
    registry,
)
from src.infrastructure.database.session import get_db_session
from src.infrastructure.github.client import GitHubClient, GitHubClientError
from src.infrastructure.github.oauth import GitHubOAuth, GitHubOAuthError

router = APIRouter(prefix="/integrations", tags=["Integrations & OAuth"])

HARNESS_LABELS = {
    "claude": "Claude Code",
    "codex": "Codex",
    "opencode": "OpenCode",
    "aider": "Aider",
}


class StartLoginRequest(BaseModel):
    harness: str = Field(..., pattern="^(claude|codex)$")


@router.get("/github/connect")
async def github_connect() -> dict[str, str]:
    """Generate GitHub OAuth 2.0 authorization URL."""
    oauth = GitHubOAuth()
    auth_url = oauth.get_authorize_url()
    return {
        "provider": "github",
        "authorize_url": auth_url,
        "client_id": settings.github.client_id,
        "app_slug": settings.github.app_slug,
    }


@router.get("/github/callback")
async def github_oauth_callback(
    code: str | None = Query(None),
    state: str | None = Query(None),
    error: str | None = Query(None),
    error_description: str | None = Query(None),
    db: AsyncSession = Depends(get_db_session),
) -> RedirectResponse:
    """Handle GitHub OAuth authorization code callback."""
    frontend_base = settings.cors_origins.split(",")[0].rstrip("/")
    if not frontend_base:
        frontend_base = "http://localhost:3000"

    if error:
        return RedirectResponse(
            url=f"{frontend_base}/workspace?github_error={error}&error_description={error_description or ''}"
        )

    if not code:
        return RedirectResponse(
            url=f"{frontend_base}/workspace?github_error=missing_code"
        )

    oauth = GitHubOAuth()
    try:
        token_data = await oauth.exchange_code(code)
    except Exception as exc:
        return RedirectResponse(
            url=f"{frontend_base}/workspace?github_error=token_exchange_failed&detail={str(exc)}"
        )

    access_token = token_data.get("access_token")
    if not access_token:
        return RedirectResponse(
            url=f"{frontend_base}/workspace?github_error=no_access_token"
        )

    token_type = token_data.get("token_type", "bearer")
    scope = token_data.get("scope", "")

    # Fetch user info from GitHub
    client = GitHubClient(access_token)
    try:
        user_info = await client.get_user()
    except Exception:
        user_info = {}
    finally:
        await client.close()

    github_user_id = str(user_info.get("id")) if user_info.get("id") else None
    username = user_info.get("login")
    avatar_url = user_info.get("avatar_url")

    # Upsert default user GitHub account
    user_id = "usr_default"
    stmt = select(GitHubAccount).where(GitHubAccount.user_id == user_id)
    res = await db.execute(stmt)
    account = res.scalar_one_or_none()

    if account:
        account.access_token = access_token
        account.token_type = token_type
        account.scope = scope
        account.github_user_id = github_user_id
        account.username = username
        account.avatar_url = avatar_url
        account.is_active = True
    else:
        account = GitHubAccount(
            user_id=user_id,
            access_token=access_token,
            token_type=token_type,
            scope=scope,
            github_user_id=github_user_id,
            username=username,
            avatar_url=avatar_url,
            is_active=True,
        )
        db.add(account)

    await db.commit()

    return RedirectResponse(
        url=f"{frontend_base}/workspace?github=connected&user={username or ''}"
    )


@router.get("/github/status")
async def get_github_status(
    db: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    """Get active user GitHub integration status."""
    stmt = select(GitHubAccount).where(
        GitHubAccount.user_id == "usr_default",
        GitHubAccount.is_active == True,  # noqa: E712
    )
    res = await db.execute(stmt)
    account = res.scalar_one_or_none()

    if not account or not account.access_token:
        return {
            "connected": False,
            "username": None,
            "avatar_url": None,
            "github_user_id": None,
            "scope": None,
            "app_slug": settings.github.app_slug,
            "app_id": settings.github.app_id,
        }

    return {
        "connected": True,
        "username": account.username,
        "avatar_url": account.avatar_url,
        "github_user_id": account.github_user_id,
        "scope": account.scope,
        "app_slug": settings.github.app_slug,
        "app_id": settings.github.app_id,
    }


@router.get("/github/repos")
async def get_github_repos(
    db: AsyncSession = Depends(get_db_session),
) -> list[dict[str, Any]]:
    """List accessible GitHub repositories for the connected user."""
    stmt = select(GitHubAccount).where(
        GitHubAccount.user_id == "usr_default",
        GitHubAccount.is_active == True,  # noqa: E712
    )
    res = await db.execute(stmt)
    account = res.scalar_one_or_none()

    if not account or not account.access_token:
        raise HTTPException(status_code=401, detail="GitHub not connected. Please authorize GitHub first.")

    client = GitHubClient(account.access_token)
    try:
        repos = await client.list_user_repos(per_page=100)
        return repos
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Failed to fetch repositories from GitHub: {str(exc)}")
    finally:
        await client.close()


@router.delete("/github/disconnect")
async def disconnect_github(
    db: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    """Disconnect GitHub and deactivate stored access token."""
    stmt = select(GitHubAccount).where(GitHubAccount.user_id == "usr_default")
    res = await db.execute(stmt)
    account = res.scalar_one_or_none()

    if account:
        if account.access_token:
            oauth = GitHubOAuth()
            try:
                await oauth.revoke_token(account.access_token)
            except Exception:
                pass
        account.is_active = False
        account.access_token = ""
        await db.commit()

    return {"connected": False, "disconnected": True}


@router.get("/status/{project_id}")
async def get_integrations_status(
    project_id: str,
    db: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    """Report connected integrations for the project."""
    project_res = await db.execute(select(Project).where(Project.id == project_id))
    project = project_res.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    host_res = await db.execute(select(Host).where(Host.project_id == project_id))
    host = host_res.scalar_one_or_none()
    lanes_res = await db.execute(select(Lane).where(Lane.host_id == host.id if host else False))
    lane_ids = [lane.id for lane in lanes_res.scalars().all()]

    harness_states: dict[str, str] = {h: "disconnected" for h in HARNESS_LABELS}
    if lane_ids:
        auth_res = await db.execute(
            select(HarnessAuth).where(HarnessAuth.lane_id.in_(lane_ids))
        )
        for row in auth_res.scalars().all():
            current = harness_states.get(row.harness, "disconnected")
            rank = {"disconnected": 0, "error": 1, "awaiting_user": 2, "connected": 3}
            if rank.get(row.state, 0) >= rank.get(current, 0):
                harness_states[row.harness] = row.state

    # Check user github connection
    gh_res = await db.execute(
        select(GitHubAccount).where(
            GitHubAccount.user_id == "usr_default",
            GitHubAccount.is_active == True,  # noqa: E712
        )
    )
    gh_account = gh_res.scalar_one_or_none()

    return {
        "project_id": project_id,
        "github": {
            "connected": bool(project.repo_full_name) or (gh_account is not None),
            "username": gh_account.username if gh_account else None,
            "app_id": settings.github.app_id,
            "app_slug": settings.github.app_slug,
            "install_url": f"https://github.com/apps/{settings.github.app_slug}/installations/new",
            "repo": project.repo_full_name,
        },
        "harnesses": {
            name: {
                "label": HARNESS_LABELS[name],
                "state": harness_states.get(name, "disconnected"),
                "credential_path": credential_path(name),
                "supports_login": name in SUPPORTED_HARNESSES,
            }
            for name in HARNESS_LABELS
        },
    }


@router.get("/github/install-url")
async def get_github_install_url() -> dict[str, str]:
    """Return public GitHub App installation URL for repository authorization."""
    return {
        "install_url": f"https://github.com/apps/{settings.github.app_slug}/installations/new",
        "app_slug": settings.github.app_slug,
    }



async def _get_lane_or_404(db: AsyncSession, lane_id: str) -> Lane:
    res = await db.execute(select(Lane).where(Lane.id == lane_id))
    lane = res.scalar_one_or_none()
    if lane is None:
        raise HTTPException(status_code=404, detail="Lane not found")
    return lane


async def _upsert_state(
    db: AsyncSession,
    lane_id: str,
    harness: str,
    state: str,
    *,
    error_code: str | None = None,
) -> HarnessAuth:
    res = await db.execute(
        select(HarnessAuth).where(
            HarnessAuth.lane_id == lane_id,
            HarnessAuth.harness == harness,
        )
    )
    row = res.scalar_one_or_none()
    now = datetime.now(timezone.utc)

    if row is None:
        row = HarnessAuth(lane_id=lane_id, harness=harness, state=state)
        db.add(row)

    row.state = state
    row.error_code = error_code
    row.checked_at = now
    row.connected_at = now if state == "connected" else None
    return row


@router.post("/harnesses/{harness}/login/{lane_id}")
async def start_harness_login(
    harness: str,
    lane_id: str,
    db: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    """Begin a host-native login for a harness on a lane.

    Starts a login session and returns the command the host runs. The control
    plane executes it in a PTY and never observes the credential. A subsequent
    poll reports progress, including the single-use authorize URL once the CLI
    binds its loopback callback port.
    """
    await _get_lane_or_404(db, lane_id)

    try:
        command = build_login_command(harness)
    except HarnessLoginError as exc:
        raise HTTPException(status_code=400, detail=exc.message) from exc

    session = registry.start(lane_id, harness)
    await _upsert_state(db, lane_id, harness, "awaiting_user")
    await db.commit()

    return {
        "lane_id": lane_id,
        "harness": harness,
        "label": HARNESS_LABELS.get(harness, harness),
        "state": session.state,
        # The command is executed on the host. We relay it, we do not run it.
        "host_command": command,
        "credential_path": credential_path(harness),
        "note": (
            "Runs the vendor's own login on the host. Congruence never receives "
            "the credential; the CLI writes it to the host's $HOME."
        ),
    }


@router.get("/harnesses/{harness}/login/{lane_id}")
async def get_harness_login_status(
    harness: str,
    lane_id: str,
    db: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    """Poll an in-flight host-native login."""
    await _get_lane_or_404(db, lane_id)
    session = registry.get(lane_id)

    if session is None:
        res = await db.execute(
            select(HarnessAuth).where(
                HarnessAuth.lane_id == lane_id,
                HarnessAuth.harness == harness,
            )
        )
        row = res.scalar_one_or_none()
        return {
            "lane_id": lane_id,
            "harness": harness,
            "state": row.state if row else "disconnected",
            "authorize_url": None,
            "device_code": None,
            "seconds_remaining": 0,
            "error_code": row.error_code if row else None,
            "error_message": None,
        }

    if session.state != "awaiting_user":
        await _upsert_state(
            db,
            lane_id,
            harness,
            session.state,
            error_code=session.error_code,
        )
        await db.commit()

    return session.to_public_dict()


@router.post("/harnesses/{harness}/disconnect/{lane_id}")
async def disconnect_harness(
    harness: str,
    lane_id: str,
    db: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    """Mark a harness disconnected after deleting its credential on the host.

    Congruence cannot perform vendor-side revocation because it holds no token
    to present. This endpoint records the local state change; the host is
    responsible for removing the credential file. Irreversible by design.
    """
    await _get_lane_or_404(db, lane_id)
    registry.clear(lane_id)
    await _upsert_state(db, lane_id, harness, "disconnected")
    await db.commit()

    return {
        "lane_id": lane_id,
        "harness": harness,
        "state": "disconnected",
        "warning": (
            "Congruence cannot restore these credentials later, because it never "
            "held them. The user must sign in again with the vendor."
        ),
    }
