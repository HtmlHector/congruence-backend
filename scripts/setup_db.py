"""Database setup and migration helper script.

Checks database connectivity and runs Alembic migrations if available.
"""

from __future__ import annotations

import os
import socket
import sys
import urllib.parse

from alembic.config import Config

from alembic import command


def check_tcp_connection(host: str, port: int, timeout_sec: float = 2.0) -> bool:
    """Check if a TCP host:port is reachable."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(timeout_sec)
    try:
        sock.connect((host, port))
        return True
    except Exception:
        return False
    finally:
        sock.close()


def run_migrations() -> None:
    """Run alembic upgrade head using alembic.ini."""
    ini_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "alembic.ini")
    alembic_cfg = Config(ini_path)
    command.upgrade(alembic_cfg, "head")


def main() -> None:
    from src.config.settings import settings

    db_url = settings.database.url
    print(f"\n[setup-db] Target Database URL: {db_url}")

    if db_url.startswith("sqlite"):
        print("[setup-db] SQLite database detected. Applying migrations...")
        try:
            run_migrations()
            print("[setup-db] SQLite migrations applied successfully.")
        except Exception as e:
            print(f"[setup-db] Error applying migrations: {e}", file=sys.stderr)
            sys.exit(1)
        return

    # For postgres / network databases, extract host & port
    parsed = urllib.parse.urlsplit(db_url)
    host = parsed.hostname or "localhost"
    port = parsed.port or 5432

    print(f"[setup-db] Checking connection to {host}:{port}...")
    is_reachable = check_tcp_connection(host, port, timeout_sec=2.0)

    if not is_reachable:
        print("\n" + "=" * 65)
        print(f"⚠️  PostgreSQL is not reachable at {host}:{port}.")
        print("    If using Docker, start local dependencies with:")
        print("      make docker-up")
        print("    Once Postgres is ready, apply migrations with:")
        print("      make migrate")
        print("=" * 65 + "\n")
        return

    print(f"[setup-db] Database server reachable at {host}:{port}. Running migrations...")
    try:
        run_migrations()
        print("[setup-db] ✅ Database migrations applied successfully.")
    except Exception as exc:
        print(f"\n⚠️  Failed to apply migrations: {exc}", file=sys.stderr)
        print("Please check database credentials or run 'make migrate' once ready.\n")


if __name__ == "__main__":
    main()
