"""RabbitMQ topic exchanges, routing keys, and queue topology declarations."""

from __future__ import annotations

from typing import Final

# Topic Exchange
EVENTS_EXCHANGE: Final[str] = "parabox.events"

# Routing Key Patterns
RK_NOTIFICATIONS_EMAIL: Final[str] = "notifications.email"
RK_AUDIT_LOG: Final[str] = "audit.log"
RK_PROJECTS_CREATED: Final[str] = "projects.created"
RK_PROJECTS_UPDATED: Final[str] = "projects.updated"
RK_PROJECTS_DELETED: Final[str] = "projects.deleted"
RK_PROJECTS_ALL: Final[str] = "projects.*"

# Queues
QUEUE_NOTIFICATIONS: Final[str] = "parabox.queue.notifications"
QUEUE_PROJECT_EVENTS: Final[str] = "parabox.queue.project_events"
QUEUE_AUDIT_EVENTS: Final[str] = "parabox.queue.audit_events"
