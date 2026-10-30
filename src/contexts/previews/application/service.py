"""Preview application service managing tickets, permissions, and port bridges."""

from __future__ import annotations

from uuid import UUID
from sqlalchemy.ext.asyncio import AsyncSession
import structlog

from src.config.settings import settings
from src.contexts.devbox.application.service import DevboxService
from src.contexts.devbox.infrastructure.repository import DevboxRepository
from src.contexts.previews.domain.entities import PREVIEW_PURPOSE, PreviewTicketClaims
from src.contexts.previews.infrastructure.forwarder import DevboxPortBridge, forwarder_pool
from src.contexts.projects.application.service import ProjectService
from src.security.tickets import TicketError, sign_ticket, verify_ticket

logger = structlog.get_logger(__name__)


class PreviewError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class PreviewService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._projects = ProjectService(session)
        self._devboxes = DevboxService(session)

    async def mint_ticket(
        self,
        project_id: UUID,
        workspace_id: UUID,
        user_id: str,
    ) -> str:
        """Create a signed ticket granting browser preview access."""
        project = await self._projects.get_project(project_id, workspace_id)
        if project is None:
            raise PreviewError("PROJECT_NOT_FOUND", "Project not found")

        box = await self._devboxes.get_live(project_id, workspace_id)
        if box is None or not box.provider_box_id:
            raise PreviewError("BOX_NOT_RUNNING", "Devbox is not running")

        ticket = sign_ticket(
            {
                "purpose": PREVIEW_PURPOSE,
                "project_id": str(project_id),
                "workspace_id": str(workspace_id),
                "user_id": user_id,
                "box_id": box.provider_box_id,
            },
            secret=settings.preview.cookie_secret,
            ttl_seconds=settings.preview.cookie_ttl_seconds,
        )
        return ticket

    def verify_ticket_or_token(self, token: str) -> PreviewTicketClaims:
        """Validate signature and claims of a preview ticket or cookie."""
        try:
            verified = verify_ticket(token, secret=settings.preview.cookie_secret)
        except TicketError as exc:
            raise PreviewError("INVALID_TICKET", str(exc)) from exc

        claims = verified.claims
        if claims.get("purpose") != PREVIEW_PURPOSE:
            raise PreviewError("INVALID_TICKET", "Invalid ticket purpose")

        return PreviewTicketClaims(
            purpose=claims["purpose"],
            project_id=claims["project_id"],
            workspace_id=claims["workspace_id"],
            user_id=claims["user_id"],
            box_id=claims["box_id"],
            expires_at=verified.expires_at,
        )

    async def get_bridge(self, box_id: str, port: int) -> DevboxPortBridge:
        """Retrieve or initialize the TCP forwarder bridge for this box and port."""
        provider = settings.devbox.provider
        return await forwarder_pool.get_or_create(box_id, port, provider=provider)
