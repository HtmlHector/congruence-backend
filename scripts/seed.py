"""Local development database seeder script.

Seeds sample users, workspaces, memberships, subscriptions, projects, and audit logs.
"""

from __future__ import annotations

import asyncio
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession
from uuid6 import uuid7

from src.contexts.audit.domain.entities import AuditLogEntry
from src.contexts.audit.infrastructure.repository import AuditLogRepository
from src.contexts.billing.domain.entities import PlanTier, Subscription, SubscriptionStatus
from src.contexts.billing.infrastructure.repository import SubscriptionRepository
from src.contexts.identity.domain.entities import User
from src.contexts.identity.infrastructure.repository import UserRepository
from src.contexts.projects.domain.entities import Project, ProjectStatus
from src.contexts.projects.infrastructure.repository import ProjectRepository
from src.contexts.workspace.domain.entities import Workspace, WorkspaceRole
from src.contexts.workspace.infrastructure.repository import WorkspaceRepository
from src.infrastructure.database.engine import async_session_factory

# Deterministic UUIDs for local testing
DEV_USER_ID = UUID("00000000-0000-0000-0000-000000000001")
DEV_WORKSPACE_ID = UUID("00000000-0000-0000-0000-000000000002")


async def seed_data(session: AsyncSession) -> None:
    user_repo = UserRepository(session)
    ws_repo = WorkspaceRepository(session)
    sub_repo = SubscriptionRepository(session)
    project_repo = ProjectRepository(session)
    audit_repo = AuditLogRepository(session)

    print("[seed] Checking existing seed data...")
    existing_user = await user_repo.get_by_id(DEV_USER_ID)
    if existing_user:
        print("[seed] Sample user already exists. Skipping seed.")
        return

    print("[seed] Creating dev user and workspace...")
    dev_user = User(
        id=DEV_USER_ID,
        clerk_id="user_dev_sample_123",
        email="founder@parabox.so",
        first_name="Alex",
        last_name="Founder",
    )
    await user_repo.add(dev_user)

    dev_workspace = Workspace(
        id=DEV_WORKSPACE_ID,
        name="Acme Corp",
        slug="acme-corp",
        stripe_customer_id="cus_dev_sample_123",
    )
    await ws_repo.add(dev_workspace)

    # Membership
    await ws_repo.add_member(
        workspace_id=DEV_WORKSPACE_ID,
        user_id=DEV_USER_ID,
        role=WorkspaceRole.OWNER,
    )

    # Subscription (Active Pro tier)
    subscription = Subscription(
        id=uuid7(),
        workspace_id=DEV_WORKSPACE_ID,
        stripe_subscription_id="sub_dev_sample_123",
        stripe_customer_id="cus_dev_sample_123",
        stripe_price_id="price_pro_dev",
        tier=PlanTier.PRO,
        status=SubscriptionStatus.ACTIVE,
    )
    await sub_repo.add(subscription)

    # Sample Projects
    projects_data = [
        ("Core API Gateway", "Central ingress router and rate limiter for client requests."),
        (
            "Realtime WebSocket Service",
            "Pub/sub event distributor handling presence & room broadcasts.",
        ),
        ("Integrations Dispatcher", "Asynchronous worker pool processing third-party webhooks."),
    ]

    for name, desc in projects_data:
        p = Project(
            id=uuid7(),
            workspace_id=DEV_WORKSPACE_ID,
            name=name,
            description=desc,
            status=ProjectStatus.ACTIVE,
            metadata={"environment": "production", "version": "1.0.0"},
        )
        await project_repo.add(p)

    # Sample Audit Log Entries
    audit_events: list[dict[str, Any]] = [
        {
            "action": "workspace.create",
            "resource_type": "workspace",
            "resource_id": str(DEV_WORKSPACE_ID),
            "payload": {"name": "Acme Corp"},
        },
        {
            "action": "subscription.upgrade",
            "resource_type": "subscription",
            "resource_id": str(subscription.id),
            "payload": {"tier": "pro"},
        },
    ]

    for evt in audit_events:
        log_entry = AuditLogEntry(
            id=uuid7(),
            workspace_id=DEV_WORKSPACE_ID,
            actor_id=str(DEV_USER_ID),
            action=evt["action"],
            resource_type=evt["resource_type"],
            resource_id=evt["resource_id"],
            payload=evt["payload"],
        )
        await audit_repo.add(log_entry)

    await session.commit()
    print("[seed] ✅ Successfully seeded local development database!")
    print(f"       User ID:      {DEV_USER_ID} (founder@parabox.so)")
    print(f"       Workspace ID: {DEV_WORKSPACE_ID} (acme-corp)")


async def main() -> None:
    import socket
    import urllib.parse

    from src.config.settings import settings

    db_url = settings.database.url
    if not db_url.startswith("sqlite"):
        parsed = urllib.parse.urlsplit(db_url)
        host = parsed.hostname or "localhost"
        port = parsed.port or 5432
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(2.0)
        try:
            sock.connect((host, port))
        except Exception:
            print(f"⚠️  Cannot connect to Postgres at {host}:{port}. Run 'make docker-up' first.")
            return
        finally:
            sock.close()

    async with async_session_factory() as session:
        await seed_data(session)


if __name__ == "__main__":
    asyncio.run(main())
