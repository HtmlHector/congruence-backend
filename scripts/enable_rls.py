"""Enable PostgreSQL row-level security tenant policies (idempotent).

Fresh databases get these policies from alembic migration 0001, but databases
whose tables were created by ``Base.metadata.create_all`` never had them. This
script brings an existing database up to the same fail-closed state and is
safe to run repeatedly.

Note: policies only bind for non-superuser connections. The application should
connect as a limited role for the boundary to take effect at the DB level;
until then the policies are in place and application-level checks are the
active enforcement layer.

Usage: uv run python scripts/enable_rls.py
"""

from __future__ import annotations

import asyncio

from sqlalchemy import text

from src.infrastructure.database.engine import async_engine

RLS_TABLES = [
    ("workspace", "workspaces"),
    ("workspace", "memberships"),
    ("workspace", "invitations"),
    ("workspace", "projects"),
    ("subscription", "subscriptions"),
    ("audit", "audit_log"),
]

POLICY_TEMPLATE = """
    CREATE POLICY tenant_isolation_{schema}_{table} ON {schema}.{table}
    FOR ALL USING (workspace_id = current_setting('app.current_workspace_id', true))
"""


async def main() -> None:
    async with async_engine.begin() as conn:
        for schema, table in RLS_TABLES:
            present = (
                await conn.execute(
                    text(
                        "SELECT 1 FROM pg_tables WHERE schemaname = :s AND tablename = :t"
                    ),
                    {"s": schema, "t": table},
                )
            ).first()
            if not present:
                # Table may not exist yet on databases where the frontend has
                # not created it (it is provisioned ad-hoc by the Next.js app).
                print(f"skip {schema}.{table} (table not present)")
                continue
            await conn.execute(text(f"ALTER TABLE {schema}.{table} ENABLE ROW LEVEL SECURITY"))
            await conn.execute(text(f"ALTER TABLE {schema}.{table} FORCE ROW LEVEL SECURITY"))
            policy_name = f"tenant_isolation_{schema}_{table}"
            existing = (
                await conn.execute(
                    text(
                        "SELECT 1 FROM pg_policies WHERE schemaname = :s AND policyname = :p"
                    ),
                    {"s": schema, "p": policy_name},
                )
            ).first()
            if existing:
                print(f"ok   {schema}.{table} (policy already present)")
            else:
                await conn.execute(
                    text(POLICY_TEMPLATE.format(schema=schema, table=table))
                )
                print(f"new  {schema}.{table} (policy created)")

        # Verify end state
        rows = (
            await conn.execute(
                text(
                    "SELECT schemaname, tablename, rowsecurity FROM pg_tables "
                    "WHERE schemaname IN ('workspace', 'subscription', 'audit') "
                    "ORDER BY 1, 2"
                )
            )
        ).fetchall()
        print("\nRLS state:")
        for sch, tbl, rls in rows:
            print(f"  {sch}.{tbl}: rowsecurity={rls}")


if __name__ == "__main__":
    asyncio.run(main())
