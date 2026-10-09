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

import hashlib
import hmac
import json
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.config.settings import settings
from src.contexts.congruence.models import (
    ActivityEvent,
    GitHubAccount,
    HarnessAuth,
    Host,
    Lane,
    Project,
    Publication,
)
from src.infrastructure.auth.dependencies import get_current_user
from src.infrastructure.auth.harness_login import (
    LOGIN_BLOCKERS,
    MAX_LOGIN_SECONDS,
    SUPPORTED_HARNESSES,
    HarnessLoginError,
    build_login_command,
    credential_path,
    is_harness_credential_present,
    login_flow,
    registry,
)
from src.infrastructure.auth.jwt import UserClaims
from src.infrastructure.auth.tenancy import require_lane_access, require_project_access
from src.infrastructure.database.session import get_db_session
from src.infrastructure.github.client import GitHubClient
from src.infrastructure.github.oauth import GitHubOAuth, issue_oauth_state, verify_oauth_state

router = APIRouter(prefix="/integrations", tags=["Integrations & OAuth"])

HARNESS_LABELS = {
    "claude": "Claude Code",
    "codex": "Codex",
    "antigravity": "Antigravity",
    "opencode": "OpenCode",
    "aider": "Aider",
}


class StartLoginRequest(BaseModel):
    harness: str = Field(..., pattern="^(claude|codex|antigravity|opencode)$")


class ConnectPATRequest(BaseModel):
    token: str = Field(..., min_length=1)


@router.post("/github/connect-pat")
async def connect_github_pat(
    payload: ConnectPATRequest,
    user: UserClaims = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    """Connect GitHub account using a Personal Access Token (PAT)."""
    raw_token = payload.token.strip()
    if not raw_token:
        raise HTTPException(status_code=400, detail="Personal access token is required")

    client = GitHubClient(raw_token)
    try:
        user_info = await client.get_user()
    except Exception as exc:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid GitHub Personal Access Token or network error: {exc!s}",
        )
    finally:
        await client.close()

    if not user_info or not user_info.get("login"):
        raise HTTPException(
            status_code=400,
            detail="Failed to retrieve GitHub profile with provided token.",
        )

    github_user_id = str(user_info.get("id")) if user_info.get("id") else None
    username = user_info.get("login")
    avatar_url = user_info.get("avatar_url")

    # Upsert the signed-in user's GitHub account
    user_id = user.user_id
    stmt = select(GitHubAccount).where(GitHubAccount.user_id == user_id)
    res = await db.execute(stmt)
    account = res.scalar_one_or_none()

    if account:
        account.access_token = raw_token
        account.token_type = "bearer"
        account.scope = "repo,user"
        account.github_user_id = github_user_id
        account.username = username
        account.avatar_url = avatar_url
        account.is_active = True
    else:
        account = GitHubAccount(
            user_id=user_id,
            access_token=raw_token,
            token_type="bearer",
            scope="repo,user",
            github_user_id=github_user_id,
            username=username,
            avatar_url=avatar_url,
            is_active=True,
        )
        db.add(account)

    await db.commit()

    return {
        "connected": True,
        "username": username,
        "avatar_url": avatar_url,
        "github_user_id": github_user_id,
    }


@router.get("/github/connect")
async def github_connect(user: UserClaims = Depends(get_current_user)) -> dict[str, str]:
    """Generate GitHub OAuth 2.0 authorization URL bound to the caller."""
    oauth = GitHubOAuth()
    state = issue_oauth_state(user.user_id)
    auth_url = oauth.get_authorize_url(state=state)
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

    # The callback carries no Authorization header; the signed state issued by
    # /github/connect identifies which user initiated this OAuth flow.
    oauth_user_id = verify_oauth_state(state)
    if oauth_user_id is None:
        return RedirectResponse(
            url=f"{frontend_base}/workspace?github_error=invalid_state"
        )

    oauth = GitHubOAuth()
    try:
        token_data = await oauth.exchange_code(code)
    except Exception as exc:
        return RedirectResponse(
            url=f"{frontend_base}/workspace?github_error=token_exchange_failed&detail={exc!s}"
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

    # Upsert the GitHub account for the user identified by the signed state
    user_id = oauth_user_id
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
    user: UserClaims = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    """Get active user GitHub integration status."""
    stmt = select(GitHubAccount).where(
        GitHubAccount.user_id == user.user_id,
        GitHubAccount.is_active == True,
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
    user: UserClaims = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> list[dict[str, Any]]:
    """List accessible GitHub repositories for the connected user."""
    stmt = select(GitHubAccount).where(
        GitHubAccount.user_id == user.user_id,
        GitHubAccount.is_active == True,
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
        raise HTTPException(status_code=502, detail=f"Failed to fetch repositories from GitHub: {exc!s}")
    finally:
        await client.close()


@router.delete("/github/disconnect")
async def disconnect_github(
    user: UserClaims = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    """Disconnect GitHub and deactivate stored access token."""
    stmt = select(GitHubAccount).where(GitHubAccount.user_id == user.user_id)
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
    user: UserClaims = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    """Report connected integrations for the project."""
    await require_project_access(db, user, project_id)
    project_res = await db.execute(select(Project).where(Project.id == project_id))
    project = project_res.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    host_res = await db.execute(select(Host).where(Host.project_id == project_id))
    host = host_res.scalar_one_or_none()
    lanes_res = await db.execute(select(Lane).where(Lane.host_id == host.id if host else False))
    lane_ids = [lane.id for lane in lanes_res.scalars().all()]

    harness_states: dict[str, str] = {
        h: "connected" if is_harness_credential_present(h) else "disconnected"
        for h in HARNESS_LABELS
    }
    if lane_ids:
        auth_res = await db.execute(
            select(HarnessAuth).where(HarnessAuth.lane_id.in_(lane_ids))
        )
        # A pending or failed handshake is only trustworthy for as long as the
        # login registry itself keeps sessions (MAX_LOGIN_SECONDS). An
        # "awaiting_user" row past that age means the user abandoned sign-in;
        # reporting it forever would block the "not connected" affordances.
        stale_cutoff = datetime.now(UTC) - timedelta(seconds=MAX_LOGIN_SECONDS)
        for row in auth_res.scalars().all():
            state = row.state
            if state in ("awaiting_user", "error") and row.checked_at is not None:
                checked = row.checked_at
                if checked.tzinfo is None:
                    checked = checked.replace(tzinfo=UTC)
                if checked < stale_cutoff:
                    state = "connected" if is_harness_credential_present(row.harness) else "disconnected"
            current = harness_states.get(row.harness, "disconnected")
            rank = {"disconnected": 0, "error": 1, "awaiting_user": 2, "connected": 3}
            if rank.get(state, 0) >= rank.get(current, 0):
                harness_states[row.harness] = state

        # Persist connected state to database
        for h, st in harness_states.items():
            if st == "connected" and lane_ids:
                await _upsert_state(db, lane_ids[0], h, "connected")
        try:
            await db.commit()
        except Exception:
            await db.rollback()

    # Check user github connection
    gh_res = await db.execute(
        select(GitHubAccount).where(
            GitHubAccount.user_id == user.user_id,
            GitHubAccount.is_active == True,
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
                "flow": login_flow(name) if name in SUPPORTED_HARNESSES else None,
                # Why sign-in cannot be relayed, when it cannot.
                "note": None if name in SUPPORTED_HARNESSES else LOGIN_BLOCKERS.get(name),
            }
            for name in HARNESS_LABELS
        },
    }


@router.get("/github/install-url")
async def get_github_install_url(
    user: UserClaims = Depends(get_current_user),
) -> dict[str, str]:
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
    now = datetime.now(UTC)

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
    user: UserClaims = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    """Begin a host-native login for a harness on a lane.

    Starts a login session and returns the command the host runs. The control
    plane executes it in a PTY and never observes the credential. A subsequent
    poll reports progress, including the single-use authorize URL once the CLI
    binds its loopback callback port.
    """
    await _get_lane_or_404(db, lane_id)
    await require_lane_access(db, user, lane_id)

    try:
        command = build_login_command(harness)
    except HarnessLoginError as exc:
        raise HTTPException(status_code=400, detail=exc.message) from exc

    flow = login_flow(harness)
    if flow == "url_paste":
        note = (
            "Runs the vendor's own sign-in on the host. The CLI prints a one-time "
            "authorization URL in the session terminal; open it on any device and "
            "paste the code back into the session. Congruence never receives "
            "the credential; the CLI writes it to the host's OS keyring."
        )
    elif flow == "interactive":
        note = (
            "Runs the vendor's own sign-in inside the session terminal; pick your "
            "provider and follow its prompts. Any vendor authorize link passes "
            "through the session output unmodified. Congruence never receives "
            "the credential."
        )
    else:
        note = (
            "Runs the vendor's own login on the host. Congruence never receives "
            "the credential; the CLI writes it to the host's $HOME."
        )

    session = registry.start(lane_id, harness)
    await _upsert_state(db, lane_id, harness, "awaiting_user")
    await db.commit()

    return {
        "lane_id": lane_id,
        "harness": harness,
        "label": HARNESS_LABELS.get(harness, harness),
        "state": session.state,
        "flow": flow,
        # The command is executed on the host. We relay it, we do not run it.
        "host_command": command,
        "credential_path": credential_path(harness),
        "note": note,
    }


@router.get("/harnesses/{harness}/login/{lane_id}")
async def get_harness_login_status(
    harness: str,
    lane_id: str,
    user: UserClaims = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    """Poll an in-flight host-native login."""
    await _get_lane_or_404(db, lane_id)
    await require_lane_access(db, user, lane_id)
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
    user: UserClaims = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    """Mark a harness disconnected after deleting its credential on the host.

    Congruence cannot perform vendor-side revocation because it holds no token
    to present. This endpoint records the local state change; the host is
    responsible for removing the credential file. Irreversible by design.
    """
    await _get_lane_or_404(db, lane_id)
    await require_lane_access(db, user, lane_id)
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


@router.post("/github/webhooks")
@router.post("/github/webhook")
async def handle_github_webhook(
    request: Request,
    x_github_event: str | None = Header(None, alias="X-GitHub-Event"),
    x_hub_signature_256: str | None = Header(None, alias="X-Hub-Signature-256"),
    db: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    """Process incoming GitHub webhooks (PR status updates, branch push sync)."""
    raw_body = await request.body()
    webhook_secret = settings.github.webhook_secret.get_secret_value() if settings.github.webhook_secret else None

    # Validate HMAC signature. When a secret is configured a valid signature
    # is mandatory; unsigned requests are rejected.
    if webhook_secret:
        expected_sig = "sha256=" + hmac.new(
            webhook_secret.encode("utf-8"), raw_body, hashlib.sha256
        ).hexdigest()
        if not x_hub_signature_256 or not hmac.compare_digest(expected_sig, x_hub_signature_256):
            raise HTTPException(status_code=401, detail="Invalid webhook signature")

    try:
        payload = json.loads(raw_body.decode("utf-8")) if raw_body else {}
    except Exception:
        payload = {}

    event_type = x_github_event or "ping"

    if event_type == "ping":
        return {"status": "ok", "event": "ping", "message": "Pong"}

    if event_type == "pull_request":
        action = payload.get("action")
        pr_data = payload.get("pull_request", {})
        pr_number = pr_data.get("number")
        pr_url = pr_data.get("html_url")
        head_branch = pr_data.get("head", {}).get("ref")
        is_merged = pr_data.get("merged", False)

        new_status = "merged" if is_merged else ("closed" if action == "closed" else "open")

        # Find matching lane and publication
        if head_branch:
            lane_res = await db.execute(select(Lane).where(Lane.branch_name == head_branch))
            lane = lane_res.scalar_one_or_none()
            if lane:
                pub_res = await db.execute(select(Publication).where(Publication.lane_id == lane.id))
                pub = pub_res.scalar_one_or_none()
                if pub:
                    pub.status = new_status
                    if pr_number:
                        pub.pr_number = pr_number
                    if pr_url:
                        pub.pr_url = pr_url
                elif pr_number and pr_url:
                    pub = Publication(
                        lane_id=lane.id,
                        actor_id="act_github_webhook",
                        pr_number=pr_number,
                        pr_url=pr_url,
                        status=new_status,
                    )
                    db.add(pub)

                host_res = await db.execute(select(Host).where(Host.id == lane.host_id))
                host = host_res.scalar_one_or_none()
                if host:
                    event = ActivityEvent(
                        project_id=host.project_id,
                        lane_id=lane.id,
                        action_type=f"github_pr_{action}",
                        summary=f"GitHub PR #{pr_number} ({head_branch}) {new_status}",
                    )
                    db.add(event)

                await db.commit()

        return {
            "status": "processed",
            "event": "pull_request",
            "action": action,
            "pr_number": pr_number,
            "pr_status": new_status,
        }

    if event_type == "push":
        ref = payload.get("ref", "")
        branch_name = ref.replace("refs/heads/", "")
        head_commit = payload.get("head_commit", {})
        commit_sha = head_commit.get("id")
        commit_msg = head_commit.get("message", "")

        lane_res = await db.execute(select(Lane).where(Lane.branch_name == branch_name))
        lane = lane_res.scalar_one_or_none()
        if lane:
            host_res = await db.execute(select(Host).where(Host.id == lane.host_id))
            host = host_res.scalar_one_or_none()
            if host:
                event = ActivityEvent(
                    project_id=host.project_id,
                    lane_id=lane.id,
                    action_type="github_push",
                    summary=f"Pushed to branch {branch_name}: {commit_msg[:60]} ({commit_sha[:7] if commit_sha else ''})",
                )
                db.add(event)
                await db.commit()

        return {
            "status": "processed",
            "event": "push",
            "branch": branch_name,
            "commit_sha": commit_sha,
        }

    return {"status": "ignored", "event": event_type}

