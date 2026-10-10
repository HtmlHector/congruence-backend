"""Clerk Svix webhook endpoint."""

from __future__ import annotations

from typing import Any

import structlog
from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.contexts.identity.application.service import IdentityService
from src.infrastructure.auth.clerk import clerk_service
from src.infrastructure.database.session import get_db

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/webhooks/clerk", tags=["Webhooks"])


@router.post("", status_code=status.HTTP_200_OK)
async def handle_clerk_webhook(
    request: Request,
    session: AsyncSession = Depends(get_db),
    svix_id: str | None = Header(None, alias="svix-id"),
    svix_timestamp: str | None = Header(None, alias="svix-timestamp"),
    svix_signature: str | None = Header(None, alias="svix-signature"),
) -> dict[str, Any]:
    """Verify and process incoming Clerk user/org lifecycle webhook."""
    headers = {
        "svix-id": svix_id or "",
        "svix-timestamp": svix_timestamp or "",
        "svix-signature": svix_signature or "",
    }
    body = await request.body()

    try:
        payload = clerk_service.verify_webhook(headers, body)
    except Exception as exc:
        logger.warning("clerk_webhook_verification_failed", error=str(exc))
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid webhook signature",
        )

    event_type = payload.get("type", "")
    event_data = payload.get("data", {})
    logger.info("clerk_webhook_received", event_type=event_type)

    identity_svc = IdentityService(session)
    await identity_svc.sync_clerk_webhook(event_type, event_data)

    return {"status": "success", "event": event_type}
