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

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.contexts.congruence.models import HarnessAuth, Lane, Project
from src.infrastructure.auth.harness_login import (
    SUPPORTED_HARNESSES,
    HarnessLoginError,
    build_login_command,
    credential_path,
    registry,
)
from src.infrastructure.database.session import get_db_session

router = APIRouter(prefix="/integrations", tags=["Integrations & OAuth"])

HARNESS_LABELS = {
    "claude": "Claude Code",
    "codex": "Codex",
    "opencode": "OpenCode",
    "aider": "Aider",
}


class StartLoginRequest(BaseModel):
    harness: str = Field(..., pattern="^(claude|codex)$")


@router.get("/status/{project_id}")
async def get_integrations_status(
    project_id: str,
    db: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    """Report connected integrations for the project.

    Agent rows are host-observed booleans, never tokens. GitHub reflects the
    App installation, which is an installation id and not a user credential.
    """
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
            # Prefer the strongest state seen across lanes: a harness
            # authenticated in any lane of the project is usable.
            rank = {"disconnected": 0, "error": 1, "awaiting_user": 2, "connected": 3}
            if rank.get(row.state, 0) >= rank.get(current, 0):
                harness_states[row.harness] = row.state

    github_app_id = os.getenv("GITHUB__APP_ID") or os.getenv("GITHUB_APP_ID", "4010628")

    return {
        "project_id": project_id,
        "github": {
            "connected": bool(project.repo_full_name),
            "app_id": github_app_id,
            "install_url": "https://github.com/apps/congruence-app/installations/new",
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
    app_slug = os.getenv("GITHUB__APP_SLUG") or os.getenv("GITHUB_APP_SLUG", "congruence-dev-app")
    return {
        "install_url": f"https://github.com/apps/{app_slug}/installations/new",
        "app_slug": app_slug,
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
