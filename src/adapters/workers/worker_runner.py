"""RabbitMQ worker consumers and message dispatch."""

from __future__ import annotations

from typing import Any

import structlog

from src.contexts.notification.application.service import notification_service
from src.infrastructure.messaging.bus import MessageBus
from src.infrastructure.messaging.topology import (
    QUEUE_NOTIFICATIONS,
    QUEUE_PROJECT_EVENTS,
    RK_NOTIFICATIONS_EMAIL,
    RK_PROJECTS_ALL,
)

logger = structlog.get_logger(__name__)


async def handle_notification_message(payload: dict[str, Any], _headers: dict[str, Any]) -> None:
    """Consume notifications.email message and deliver via email service."""
    to = payload.get("to", "")
    subject = payload.get("subject", "")
    html_body = payload.get("html_body", "")

    logger.info("worker_processing_email", to=to, subject=subject)
    await notification_service.deliver_email(to=to, subject=subject, html_body=html_body)


async def handle_project_event(payload: dict[str, Any], headers: dict[str, Any]) -> None:
    """Consume projects.* events for background processing."""
    project_id = payload.get("project_id")
    workspace_id = headers.get("workspace_id") or payload.get("workspace_id")
    logger.info("worker_processing_project_event", project_id=project_id, workspace_id=workspace_id)


async def register_all_workers(bus: MessageBus) -> None:
    """Register all queue consumers onto the active message bus."""
    await bus.subscribe(
        queue_name=QUEUE_NOTIFICATIONS,
        routing_keys=[RK_NOTIFICATIONS_EMAIL],
        callback=handle_notification_message,
    )
    await bus.subscribe(
        queue_name=QUEUE_PROJECT_EVENTS,
        routing_keys=[RK_PROJECTS_ALL],
        callback=handle_project_event,
    )
    logger.info("all_workers_registered")
