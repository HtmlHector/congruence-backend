"""Environment variable application service."""

from __future__ import annotations

import re
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from src.contexts.audit.application.service import AuditService
from src.contexts.environments.domain.entities import EnvScope, EnvVar
from src.contexts.environments.infrastructure.repository import EnvVarRepository
from src.security.encryption import encryption_service

_KEY_PATTERN = re.compile(r"^[A-Z_][A-Z0-9_]{0,254}$")
MASKED_VALUE = "********"


class EnvError(Exception):
    """Domain-level environment failure with a machine-readable code."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class EnvService:
    """Stores encrypted environment variables and resolves them per worktree."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._repo = EnvVarRepository(session)
        self._audit = AuditService(session)

    async def upsert(
        self,
        *,
        workspace_id: UUID,
        scope: EnvScope,
        project_id: UUID | None,
        key: str,
        value: str,
        is_secret: bool,
        actor_id: str,
    ) -> EnvVar:
        """Create or update one environment variable."""
        normalized_key = key.strip()
        if not _KEY_PATTERN.match(normalized_key):
            raise EnvError(
                "INVALID_ENV_KEY",
                "Keys must start with a letter or underscore and contain only A-Z, 0-9, _",
            )
        if scope is EnvScope.PROJECT and project_id is None:
            raise EnvError("INVALID_SCOPE", "Project-scoped variables require a project id")
        if scope is EnvScope.WORKSPACE and project_id is not None:
            raise EnvError("INVALID_SCOPE", "Workspace-scoped variables cannot set a project id")

        existing = await self._repo.get_key(workspace_id, scope, project_id, normalized_key)
        ciphertext = encryption_service.encrypt(value)

        if existing:
            existing.value_ciphertext = ciphertext
            existing.is_secret = is_secret
            existing.updated_by = actor_id
            updated = await self._repo.update(existing)
        else:
            env_var = EnvVar(
                workspace_id=workspace_id,
                scope=scope,
                project_id=project_id,
                key=normalized_key,
                value_ciphertext=ciphertext,
                is_secret=is_secret,
                updated_by=actor_id,
            )
            updated = await self._repo.add(env_var)

        await self._audit.record(
            workspace_id=workspace_id,
            actor_id=actor_id,
            action="env.update",
            resource_type="env_var",
            resource_id=str(updated.id),
            payload={
                "key": normalized_key,
                "scope": scope.value,
                "project_id": str(project_id or ""),
            },
        )
        return updated

    async def list_scope(
        self,
        *,
        workspace_id: UUID,
        scope: EnvScope,
        project_id: UUID | None = None,
    ) -> list[dict[str, str | bool]]:
        """List variables with secret values masked."""
        values = await self._repo.list_scope(workspace_id, scope, project_id)
        return [
            {
                "key": value.key,
                "value": MASKED_VALUE
                if value.is_secret
                else encryption_service.decrypt(value.value_ciphertext),
                "is_secret": value.is_secret,
                "updated_by": value.updated_by,
            }
            for value in values
        ]

    async def resolve(self, workspace_id: UUID, project_id: UUID) -> dict[str, str]:
        """Resolve workspace variables overlaid by project variables, decrypted."""
        resolved: dict[str, str] = {}
        for env_var in await self._repo.list_scope(workspace_id, EnvScope.WORKSPACE, None):
            resolved[env_var.key] = encryption_service.decrypt(env_var.value_ciphertext)
        for env_var in await self._repo.list_scope(workspace_id, EnvScope.PROJECT, project_id):
            resolved[env_var.key] = encryption_service.decrypt(env_var.value_ciphertext)
        return resolved

    async def delete(
        self,
        *,
        workspace_id: UUID,
        scope: EnvScope,
        project_id: UUID | None,
        key: str,
        actor_id: str,
    ) -> bool:
        """Delete one environment variable."""
        existing = await self._repo.get_key(workspace_id, scope, project_id, key.strip())
        if existing is None:
            return False
        deleted = await self._repo.delete(existing.id)
        if deleted:
            await self._audit.record(
                workspace_id=workspace_id,
                actor_id=actor_id,
                action="env.delete",
                resource_type="env_var",
                resource_id=str(existing.id),
                payload={"key": existing.key, "scope": scope.value},
            )
        return deleted
