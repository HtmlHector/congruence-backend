"""Agent credential repository implementation."""

from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.contexts.credentials.domain.entities import (
    AgentCredential,
    CredentialStatus,
    normalize_provider,
)
from src.contexts.credentials.infrastructure.models import AgentCredentialModel
from src.shared.tenant_repository import TenantScopedRepository


class CredentialRepository(TenantScopedRepository[AgentCredentialModel, AgentCredential]):
    """SQLAlchemy persistence for per-user agent credentials."""

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session)

    @property
    def _model_cls(self) -> type[AgentCredentialModel]:
        return AgentCredentialModel

    def _to_entity(self, model: AgentCredentialModel) -> AgentCredential:
        return AgentCredential(
            id=model.id,
            created_at=model.created_at,
            updated_at=model.updated_at,
            workspace_id=model.workspace_id,
            user_id=model.user_id,
            provider=model.provider,
            kind=model.kind,
            secret_ciphertext=model.secret_ciphertext,
            label=model.label,
            status=model.status,
            expires_at=model.expires_at,
            last_verified_at=model.last_verified_at,
        )

    def _to_model(self, entity: AgentCredential) -> AgentCredentialModel:
        return AgentCredentialModel(
            id=entity.id,
            created_at=entity.created_at,
            updated_at=entity.updated_at,
            workspace_id=entity.workspace_id,
            user_id=entity.user_id,
            provider=entity.provider,
            kind=entity.kind,
            secret_ciphertext=entity.secret_ciphertext,
            label=entity.label,
            status=entity.status,
            expires_at=entity.expires_at,
            last_verified_at=entity.last_verified_at,
        )

    async def get_active(
        self, *, workspace_id: UUID, user_id: str, provider: str
    ) -> AgentCredential | None:
        """Return the user's active credential for a provider."""
        query = (
            select(AgentCredentialModel)
            .where(
                AgentCredentialModel.workspace_id == workspace_id,
                AgentCredentialModel.user_id == user_id,
                AgentCredentialModel.provider == normalize_provider(provider),
                AgentCredentialModel.status == CredentialStatus.ACTIVE,
            )
            .limit(1)
        )
        result = await self._session.execute(query)
        model = result.scalar_one_or_none()
        return self._to_entity(model) if model else None

    async def list_for_user(self, workspace_id: UUID, user_id: str) -> Sequence[AgentCredential]:
        """List every non-revoked credential owned by a user."""
        query = (
            select(AgentCredentialModel)
            .where(
                AgentCredentialModel.workspace_id == workspace_id,
                AgentCredentialModel.user_id == user_id,
                AgentCredentialModel.status != CredentialStatus.REVOKED,
            )
            .order_by(AgentCredentialModel.created_at.asc())
        )
        result = await self._session.execute(query)
        return [self._to_entity(model) for model in result.scalars().all()]
