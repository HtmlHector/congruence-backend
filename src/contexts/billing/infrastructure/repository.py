"""Subscription repository implementation using async SQLAlchemy."""

from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.contexts.billing.domain.entities import Subscription
from src.contexts.billing.infrastructure.models import SubscriptionModel
from src.shared.repository import AbstractRepository


class SubscriptionRepository(AbstractRepository[Subscription]):
    """SQLAlchemy implementation of subscription persistence."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    def _to_entity(self, model: SubscriptionModel) -> Subscription:
        return Subscription(
            id=model.id,
            created_at=model.created_at,
            updated_at=model.updated_at,
            workspace_id=model.workspace_id,
            stripe_subscription_id=model.stripe_subscription_id,
            stripe_customer_id=model.stripe_customer_id,
            stripe_price_id=model.stripe_price_id,
            tier=model.tier,
            status=model.status,
            current_period_end=model.current_period_end,
            cancel_at_period_end=model.cancel_at_period_end,
        )

    async def get_by_id(self, entity_id: UUID) -> Subscription | None:
        model = await self._session.get(SubscriptionModel, entity_id)
        return self._to_entity(model) if model else None

    async def get_by_workspace_id(self, workspace_id: UUID) -> Subscription | None:
        query = select(SubscriptionModel).where(SubscriptionModel.workspace_id == workspace_id)
        result = await self._session.execute(query)
        model = result.scalar_one_or_none()
        return self._to_entity(model) if model else None

    async def get_by_stripe_subscription_id(self, sub_id: str) -> Subscription | None:
        query = select(SubscriptionModel).where(SubscriptionModel.stripe_subscription_id == sub_id)
        result = await self._session.execute(query)
        model = result.scalar_one_or_none()
        return self._to_entity(model) if model else None

    async def list_all(self, limit: int = 100, offset: int = 0) -> Sequence[Subscription]:
        query = select(SubscriptionModel).limit(limit).offset(offset)
        result = await self._session.execute(query)
        return [self._to_entity(m) for m in result.scalars().all()]

    async def add(self, entity: Subscription) -> Subscription:
        model = SubscriptionModel(
            id=entity.id,
            created_at=entity.created_at,
            updated_at=entity.updated_at,
            workspace_id=entity.workspace_id,
            stripe_subscription_id=entity.stripe_subscription_id,
            stripe_customer_id=entity.stripe_customer_id,
            stripe_price_id=entity.stripe_price_id,
            tier=entity.tier,
            status=entity.status,
            current_period_end=entity.current_period_end,
            cancel_at_period_end=entity.cancel_at_period_end,
        )
        self._session.add(model)
        await self._session.flush()
        return self._to_entity(model)

    async def update(self, entity: Subscription) -> Subscription:
        model = await self._session.get(SubscriptionModel, entity.id)
        if not model:
            raise ValueError(f"Subscription with id {entity.id} does not exist")
        model.stripe_subscription_id = entity.stripe_subscription_id
        model.stripe_customer_id = entity.stripe_customer_id
        model.stripe_price_id = entity.stripe_price_id
        model.tier = entity.tier
        model.status = entity.status
        model.current_period_end = entity.current_period_end
        model.cancel_at_period_end = entity.cancel_at_period_end
        model.updated_at = entity.updated_at
        await self._session.flush()
        return self._to_entity(model)

    async def delete(self, entity_id: UUID) -> bool:
        model = await self._session.get(SubscriptionModel, entity_id)
        if not model:
            return False
        await self._session.delete(model)
        await self._session.flush()
        return True
