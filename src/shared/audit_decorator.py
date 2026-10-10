"""Audit logging decorator primitive for service methods."""

from __future__ import annotations

from collections.abc import Callable
from functools import wraps
from typing import Any, ParamSpec, TypeVar
from uuid import UUID

import structlog

from src.contexts.audit.application.service import AuditService

logger = structlog.get_logger(__name__)

P = ParamSpec("P")
R = TypeVar("R")


def audited(
    action: str,
    resource_type: str,
    extract_resource_id: Callable[[Any], str | None] | None = None,
) -> Callable[[Callable[P, R]], Callable[P, R]]:
    """Decorator for application service methods that logs actions to AuditService.

    Expects the service class instance (`self`) to have `_session` or `_audit` initialized.
    Automatically extracts `workspace_id` and `actor_id` from keyword args or method arguments.

    Example:
        @audited(action="project.create", resource_type="project")
        async def create_project(self, workspace_id: UUID, actor_id: str, ...):
            ...
    """

    def decorator(fn: Callable[P, R]) -> Callable[P, R]:
        @wraps(fn)
        async def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            # Execute underlying method
            result = await fn(*args, **kwargs)  # type: ignore[misc]

            try:
                # Resolve self and AuditService
                self_instance = args[0] if args else None
                audit_svc: AuditService | None = None

                if hasattr(self_instance, "_audit") and isinstance(
                    self_instance._audit, AuditService
                ):
                    audit_svc = self_instance._audit
                elif hasattr(self_instance, "_session"):
                    audit_svc = AuditService(self_instance._session)

                if audit_svc:
                    # Extract workspace_id
                    workspace_id = kwargs.get("workspace_id")
                    if not workspace_id and len(args) > 1 and isinstance(args[1], UUID):
                        workspace_id = args[1]

                    # Extract actor_id
                    actor_id = kwargs.get("actor_id")
                    if not actor_id and len(args) > 2 and isinstance(args[2], str):
                        actor_id = args[2]

                    if isinstance(workspace_id, UUID) and actor_id:
                        resource_id: str | None = None
                        if extract_resource_id:
                            resource_id = extract_resource_id(result)
                        elif hasattr(result, "id"):
                            resource_id = str(result.id)

                        payload: dict[str, Any] = {}
                        if hasattr(result, "name"):
                            payload["name"] = result.name

                        await audit_svc.record(
                            workspace_id=workspace_id,
                            actor_id=str(actor_id),
                            action=action,
                            resource_type=resource_type,
                            resource_id=resource_id,
                            payload=payload,
                        )
            except Exception as exc:
                logger.warning(
                    "audit_decorator_record_failed",
                    action=action,
                    resource_type=resource_type,
                    error=str(exc),
                )

            return result  # type: ignore[no-any-return]

        return wrapper  # type: ignore[return-value]

    return decorator
