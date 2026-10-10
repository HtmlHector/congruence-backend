"""Integration and credential repository implementation using async SQLAlchemy."""

from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.contexts.integrations.domain.entities import (
    IntegrationConfig,
    IntegrationCredential,
    IntegrationProvider,
    WebhookEventLog,
)
from src.contexts.integrations.infrastructure.models import (
    IntegrationConfigModel,
    IntegrationCredentialModel,
    WebhookEventModel,
)


class IntegrationRepository:
    """Async SQLAlchemy persistence for credentials, configurations, and webhook logs."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    def _to_credential_entity(self, model: IntegrationCredentialModel) -> IntegrationCredential:
        return IntegrationCredential(
            id=model.id,
            created_at=model.created_at,
            updated_at=model.updated_at,
            workspace_id=model.workspace_id,
            provider=model.provider,
            credential_type=model.credential_type,
            encrypted_value=model.encrypted_value,
            scopes=list(model.scopes),
            expires_at=model.expires_at,
            metadata=dict(model.metadata_),
            is_active=model.is_active,
        )

    def _to_config_entity(self, model: IntegrationConfigModel) -> IntegrationConfig:
        return IntegrationConfig(
            id=model.id,
            created_at=model.created_at,
            updated_at=model.updated_at,
            workspace_id=model.workspace_id,
            provider=model.provider,
            is_enabled=model.is_enabled,
            settings=dict(model.settings),
        )

    async def get_credential(
        self, workspace_id: UUID, provider: IntegrationProvider
    ) -> IntegrationCredential | None:
        query = select(IntegrationCredentialModel).where(
            IntegrationCredentialModel.workspace_id == workspace_id,
            IntegrationCredentialModel.provider == provider,
            IntegrationCredentialModel.is_active.is_(True),
        )
        result = await self._session.execute(query)
        model = result.scalar_one_or_none()
        return self._to_credential_entity(model) if model else None

    async def list_credentials_for_workspace(
        self, workspace_id: UUID
    ) -> Sequence[IntegrationCredential]:
        query = select(IntegrationCredentialModel).where(
            IntegrationCredentialModel.workspace_id == workspace_id,
            IntegrationCredentialModel.is_active.is_(True),
        )
        result = await self._session.execute(query)
        return [self._to_credential_entity(m) for m in result.scalars().all()]

    async def upsert_credential(self, entity: IntegrationCredential) -> IntegrationCredential:
        assert entity.workspace_id is not None
        query = select(IntegrationCredentialModel).where(
            IntegrationCredentialModel.workspace_id == entity.workspace_id,
            IntegrationCredentialModel.provider == entity.provider,
        )
        result = await self._session.execute(query)
        model = result.scalar_one_or_none()

        if model:
            model.credential_type = entity.credential_type
            model.encrypted_value = entity.encrypted_value
            model.scopes = entity.scopes
            model.expires_at = entity.expires_at
            model.metadata_ = entity.metadata
            model.is_active = entity.is_active
            model.updated_at = entity.updated_at
        else:
            model = IntegrationCredentialModel(
                id=entity.id,
                created_at=entity.created_at,
                updated_at=entity.updated_at,
                workspace_id=entity.workspace_id,
                provider=entity.provider,
                credential_type=entity.credential_type,
                encrypted_value=entity.encrypted_value,
                scopes=entity.scopes,
                expires_at=entity.expires_at,
                metadata_=entity.metadata,
                is_active=entity.is_active,
            )
            self._session.add(model)

        await self._session.flush()
        return self._to_credential_entity(model)

    async def delete_credential(self, workspace_id: UUID, provider: IntegrationProvider) -> bool:
        query = select(IntegrationCredentialModel).where(
            IntegrationCredentialModel.workspace_id == workspace_id,
            IntegrationCredentialModel.provider == provider,
        )
        result = await self._session.execute(query)
        model = result.scalar_one_or_none()
        if not model:
            return False
        await self._session.delete(model)
        await self._session.flush()
        return True

    async def get_config(
        self, workspace_id: UUID, provider: IntegrationProvider
    ) -> IntegrationConfig | None:
        query = select(IntegrationConfigModel).where(
            IntegrationConfigModel.workspace_id == workspace_id,
            IntegrationConfigModel.provider == provider,
        )
        result = await self._session.execute(query)
        model = result.scalar_one_or_none()
        return self._to_config_entity(model) if model else None

    async def upsert_config(self, entity: IntegrationConfig) -> IntegrationConfig:
        assert entity.workspace_id is not None
        query = select(IntegrationConfigModel).where(
            IntegrationConfigModel.workspace_id == entity.workspace_id,
            IntegrationConfigModel.provider == entity.provider,
        )
        result = await self._session.execute(query)
        model = result.scalar_one_or_none()

        if model:
            model.is_enabled = entity.is_enabled
            model.settings = entity.settings
            model.updated_at = entity.updated_at
        else:
            model = IntegrationConfigModel(
                id=entity.id,
                created_at=entity.created_at,
                updated_at=entity.updated_at,
                workspace_id=entity.workspace_id,
                provider=entity.provider,
                is_enabled=entity.is_enabled,
                settings=entity.settings,
            )
            self._session.add(model)

        await self._session.flush()
        return self._to_config_entity(model)

    async def log_webhook_event(self, entity: WebhookEventLog) -> WebhookEventLog:
        model = WebhookEventModel(
            id=entity.id,
            created_at=entity.created_at,
            updated_at=entity.updated_at,
            workspace_id=entity.workspace_id,
            provider=entity.provider,
            event_type=entity.event_type,
            payload=entity.payload,
            processed_at=entity.processed_at,
        )
        self._session.add(model)
        await self._session.flush()
        return entity
