# Contributing to Parabox Backend Starter

Thank you for contributing to the Parabox Backend Starter foundation! This guide outlines our development workflow, coding standards, and review process.

---

## 1. Local Development Setup

We use [`uv`](https://github.com/astral-sh/uv) as the single Python package manager.

```bash
# 1. Install dependencies & configure .env
make setup

# 2. Start local infrastructure containers (PostgreSQL, Redis, RabbitMQ, MinIO)
make docker-up

# 3. Apply database schema migrations
make migrate

# 4. Start API server with hot-reload
make dev
```

---

## 2. Quality Gates & Local Checks

Before opening a pull request, ensure all local checks pass:

```bash
# 1. Lint code and check format
make lint

# 2. Run static type checking with mypy
make typecheck

# 3. Run full automated test suite
make test
```

You can also install the Git pre-commit hooks to run these automatically:
```bash
uv run pre-commit install
```

---

## 3. Architecture & Domain-Driven Design Conventions

This repository follows **Clean Architecture** and **Domain-Driven Design (DDD)**:

- **Domain:** Business entities, value objects, and domain events live in `src/contexts/<context>/domain/`. They MUST NOT depend on database or HTTP frameworks.
- **Infrastructure:** Database models, ORM mappings, external third-party SDKs live in `src/contexts/<context>/infrastructure/`.
- **Application Services:** Use cases, commands, queries, and event dispatches live in `src/contexts/<context>/application/`.
- **Adapters & Routes:** HTTP controllers live in `src/adapters/api/routes/` and workers live in `src/adapters/workers/`.

### Multi-Tenancy Rules
- Every tenant resource must reference `workspace_id`.
- Always enforce tenant scoping through `TenantContext` and `Depends(get_active_tenant)`.
- Enable PostgreSQL Row-Level Security (RLS) on any newly added table containing tenant data.

---

## 4. Database Migrations

Whenever models are modified:
1. Generate an Alembic migration revision:
   ```bash
   make migration name="describe_your_schema_change"
   ```
2. Inspect the generated migration in `alembic/versions/` to verify it includes:
   - Proper types, foreign keys, and indexes.
   - `op.execute("ALTER TABLE ... ENABLE ROW LEVEL SECURITY;")` and tenant isolation policies if workspace-scoped.
3. Test bidirectional migrations:
   ```bash
   make migrate
   uv run alembic downgrade -1
   make migrate
   ```

---

## 5. Submitting a Pull Request

1. Create a descriptive feature branch (`git checkout -b feat/your-feature-name`).
2. Commit changes using conventional commit messages (`feat: ...`, `fix: ...`, `chore: ...`).
3. Push to your branch and open a PR against `main`.
4. Fill out the pull request template completely. All CI checks must pass before merging.
