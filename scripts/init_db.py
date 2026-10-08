"""Database initialization and seeding utility for Congruence."""

from __future__ import annotations

import argparse
import asyncio

from sqlalchemy import select, text

from src.config.settings import settings
from src.contexts import (  # noqa: F401
    audit,
    billing,
    congruence,
    identity,
    workspace,
)
from src.contexts.congruence.models import (
    Actor,
    Host,
    Lane,
    Organization,
    OrganizationMember,
    Project,
)
from src.infrastructure.database.base import Base
from src.infrastructure.database.engine import async_engine
from src.infrastructure.database.session import AsyncSessionFactory


async def init_schema() -> list[str]:
    """Create all required tables depending on the database backend."""
    is_sqlite = str(async_engine.url).startswith("sqlite")
    created_tables: list[str] = []

    async with async_engine.begin() as conn:
        if is_sqlite:
            congruence_tables = [
                t for t in Base.metadata.sorted_tables if t.name.startswith("congruence_")
            ]
            await conn.run_sync(
                lambda sync_conn: Base.metadata.create_all(sync_conn, tables=congruence_tables)
            )
            created_tables = [t.name for t in congruence_tables]
        else:
            for schema in ["identity", "workspace", "subscription", "audit"]:
                await conn.execute(text(f"CREATE SCHEMA IF NOT EXISTS {schema}"))
            await conn.run_sync(Base.metadata.create_all)
            created_tables = [t.name for t in Base.metadata.sorted_tables]

    if not is_sqlite:
        async with AsyncSessionFactory() as session:
            roles = [
                ("OWNER", "Full workspace control", ["*"]),
                (
                    "ADMIN",
                    "Administrative access",
                    [
                        "workspace:read",
                        "workspace:write",
                        "member:read",
                        "member:write",
                        "apikey:*",
                        "billing:read",
                        "agent:*",
                    ],
                ),
                (
                    "MEMBER",
                    "Standard member access",
                    ["workspace:read", "agent:chat", "agent:action", "apikey:read"],
                ),
                ("VIEWER", "Read-only access", ["workspace:read"]),
            ]
            for name, desc, perms in roles:
                await session.execute(
                    text(
                        """
                        INSERT INTO identity.roles (role_id, name, description, is_system, permissions)
                        VALUES (gen_random_uuid(), :name, :desc, TRUE, :perms)
                        ON CONFLICT (name, workspace_id) DO UPDATE SET
                            description = EXCLUDED.description,
                            permissions = EXCLUDED.permissions
                        """
                    ),
                    {"name": name, "desc": desc, "perms": perms},
                )
            await session.commit()

    return created_tables


async def seed_data() -> None:
    """Seed sample data for local development."""
    async with AsyncSessionFactory() as session:
        # Check if an organization already exists
        res = await session.execute(select(Organization).limit(1))
        existing_org = res.scalars().first()
        if existing_org:
            print(f"Database already contains organization '{existing_org.name}' ({existing_org.id}). Skipping seed.")
            return

        # 1. Create Default Organization
        org = Organization(
            id="org_default",
            name="Default Organization",
            slug="default-org",
            plan_tier="pro",
            writer_seats_count=5,
            subscription_status="active",
        )
        session.add(org)
        await session.flush()

        # 2. Add Default User Membership
        member = OrganizationMember(
            id="orgm_default",
            organization_id=org.id,
            user_id="usr_default",
            role="owner",
        )
        session.add(member)

        # 3. Create Sample Project
        project = Project(
            id="proj_sample_congruence",
            organization_id=org.id,
            name="Sample Project",
            slug="sample-project",
            repo_full_name="congruence/sample-project",
            default_branch="main",
            visibility="private",
            spend_cap_usd=50.0,
            egress_limit_mb=10240,
        )
        session.add(project)
        await session.flush()

        # 4. Create Host for Project
        host = Host(
            id="host_sample_1",
            project_id=project.id,
            backend_type="process_local",
            state="awake",
            size="standard",
            cpu_count=2,
            memory_gb=4,
            disk_size_gb=32,
        )
        session.add(host)
        await session.flush()

        # 5. Create Default Lane
        lane = Lane(
            id="lane_sample_main",
            host_id=host.id,
            name="main",
            slug="main",
            worktree_path="/tmp/congruence_runners/sample-project/main",
            branch_name="main",
            is_pair_lane=False,
            status="ready",
        )
        session.add(lane)

        # 6. Create Primary Human Actor
        actor = Actor(
            id="act_owner_1",
            project_id=project.id,
            user_id="usr_default",
            actor_type="human",
            display_name="Project Owner",
            role="owner",
            avatar_color="#4F46E5",
            presence="online",
        )
        session.add(actor)

        await session.commit()
        print("Sample development data seeded successfully:")
        print(f" - Organization: {org.name} ({org.id})")
        print(f" - Project: {project.name} ({project.id})")
        print(f" - Host: {host.id} ({host.backend_type})")
        print(f" - Lane: {lane.name} ({lane.id})")


async def main() -> None:
    parser = argparse.ArgumentParser(description="Initialize Congruence Backend Database")
    parser.add_argument("--seed", action="store_true", help="Seed sample development data")
    args = parser.parse_args()

    print(f"Initializing database: {settings.database.url}")
    tables = await init_schema()
    print(f"Database schema initialized successfully with {len(tables)} tables:")
    for t in sorted(tables):
        print(f" - {t}")

    if args.seed:
        print("\nSeeding development data...")
        await seed_data()

    await async_engine.dispose()
    print("\nDone!")


if __name__ == "__main__":
    asyncio.run(main())
