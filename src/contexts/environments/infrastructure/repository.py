"""Environment variable repository implementation."""

from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.contexts.environments.domain.entities import EnvScope, EnvVar
from src.contexts.environments.infrastructure.models import EnvVarModel
from src.shared.tenant_repository import TenantScopedRepository


class EnvVarRepository(TenantScopedRepository[EnvVarModel, EnvVar]):
    """SQLAlchemy persistence for environment variables."""

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session)

    @property
    def _model_cls(self) -> type[EnvVarModel]:
        return EnvVarModel

    def _to_entity(self, model: EnvVarModel) -> EnvVar:
        return EnvVar(
            id=model.id,
            created_at=model.created_at,
            updated_at=model.updated_at,
            workspace_id=model.workspace_id,
            scope=model.scope,
            project_id=model.project_id,
            key=model.key,
            value_ciphertext=model.value_ciphertext,
            is_secret=model.is_secret,
            updated_by=model.updated_by,
        )

    def _to_model(self, entity: EnvVar) -> EnvVarModel:
        return EnvVarModel(
            id=entity.id,
            created_at=entity.created_at,
            updated_at=entity.updated_at,
            workspace_id=entity.workspace_id,
            scope=entity.scope,
            project_id=entity.project_id,
            key=entity.key,
            value_ciphertext=entity.value_ciphertext,
            is_secret=entity.is_secret,
            updated_by=entity.updated_by,
        )

    async def list_scope(
        self, workspace_id: UUID, scope: EnvScope, project_id: UUID | None
    ) -> Sequence[EnvVar]:
        """List env vars for a workspace or project scope."""
        query = select(EnvVarModel).where(
            EnvVarModel.workspace_id == workspace_id, EnvVarModel.scope == scope
        )
        if scope is EnvScope.PROJECT:
            query = query.where(EnvVarModel.project_id == project_id)
        result = await self._session.execute(query)
        return [self._to_entity(model) for model in result.scalars().all()]

    async def get_key(
        self, workspace_id: UUID, scope: EnvScope, project_id: UUID | None, key: str
    ) -> EnvVar | None:
        """Fetch a single env var by key and scope."""
        query = select(EnvVarModel).where(
            EnvVarModel.workspace_id == workspace_id,
            EnvVarModel.scope == scope,
            EnvVarModel.key == key,
        )
        if scope is EnvScope.PROJECT:
            query = query.where(EnvVarModel.project_id == project_id)
        result = await self._session.execute(query)
        model = result.scalar_one_or_none()
        return self._to_entity(model) if model else None
