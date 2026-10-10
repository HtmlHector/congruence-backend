"""Notification application service for email and alert dispatch."""

from __future__ import annotations

import structlog

from src.infrastructure.email.resend import email_service
from src.infrastructure.messaging.bus import message_bus
from src.infrastructure.messaging.topology import RK_NOTIFICATIONS_EMAIL

logger = structlog.get_logger(__name__)


class NotificationService:
    """Dispatches notifications via email and background bus."""

    async def enqueue_email(
        self,
        to: str,
        subject: str,
        html_body: str,
        workspace_id: str | None = None,
    ) -> None:
        """Enqueue email to RabbitMQ for asynchronous delivery by worker."""
        await message_bus.publish(
            routing_key=RK_NOTIFICATIONS_EMAIL,
            payload={
                "to": to,
                "subject": subject,
                "html_body": html_body,
            },
            workspace_id=workspace_id,
        )
        logger.info("email_enqueued", to=to, subject=subject)

    async def deliver_email(
        self,
        to: str,
        subject: str,
        html_body: str,
    ) -> dict[str, str]:
        """Deliver email directly via Resend."""
        return await email_service.send_email(to=to, subject=subject, html_body=html_body)


notification_service = NotificationService()
