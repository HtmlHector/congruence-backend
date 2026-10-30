# Parabox Backend Starter (`parabox-backend-starter`)

A production-ready, copyable FastAPI backend foundation for independent products. Built with Clean Architecture, Domain-Driven Design (DDD), PostgreSQL 16 (Row-Level Security), Redis, RabbitMQ, S3/MinIO, Socket.IO, Clerk Authentication, and Stripe Billing.

---

## 1. Requirements

- Python 3.12 or newer
- [`uv`](https://github.com/astral-sh/uv) (Astral Python package manager)
- Docker & Docker Compose (for local PostgreSQL, Redis, RabbitMQ, and MinIO)

---

## 2. Quickstart Locally

### Step 1: Install dependencies & create `.env`
```bash
make setup
```

### Step 2: Start local infrastructure
```bash
make docker-up
```
This spins up:
- **PostgreSQL 16** (`localhost:5432` — `parabox:parabox`, database `parabox_dev`)
- **Redis 7** (`localhost:6379`)
- **RabbitMQ 4** (`localhost:5672` — management UI at `http://localhost:15672`, `guest:guest`)
- **MinIO** (`localhost:9000` — console at `http://localhost:9001`, `minioadmin:minioadmin`)

### Step 3: Run database migrations
```bash
make migrate
```

### Step 4: Run the API and Worker processes
In terminal 1:
```bash
make dev
```
Open interactive Swagger API docs at <http://localhost:8001/docs>.

In terminal 2 (optional for background events):
```bash
make dev-worker
```

---

## 3. Architecture Overview

This starter follows **Clean Architecture** and **Domain-Driven Design (DDD)**:

```
src/
├── bootstrap/          # FastAPI app factory, DI container, lifespan, workers entrypoint
├── config/             # Pydantic Settings v2 configuration
├── shared/             # BaseEntity, BaseAggregateRoot, DomainEvent, TenantContext, Repository
├── infrastructure/     # Database (SQLAlchemy 2.0 + RLS), Redis, RabbitMQ, S3, Clerk, Stripe
├── realtime/           # Socket.IO AsyncServer with Redis manager & event broadcast
├── contexts/
│   ├── identity/       # Users synced with Clerk (JIT provisioning & webhooks)
│   ├── workspace/      # Multi-tenant organizations, memberships & RBAC
│   ├── billing/        # Stripe customers, checkout sessions, portal & subscription tiers
│   ├── audit/          # Immutable append-only audit trail
│   ├── notification/   # Transactional emails (Resend) and event queues
│   ├── integrations/   # Multi-provider integrations (OAuth, webhooks, AES-encrypted tokens)
│   └── projects/       # Reference CRUD domain context with events and RLS
└── adapters/
    ├── api/            # Route controllers (/api/v1/...) & FastAPI dependencies
    └── workers/        # RabbitMQ queue consumers
```

---

## 4. Key Primitives

### Authentication & User Sync (Clerk)
- Verifies Bearer JWT tokens via Clerk's JWKS endpoint (RS256).
- JIT (Just-In-Time) provisions local `users` records in `/api/v1/auth/me` if webhooks have not landed yet.
- Verifies and processes Svix webhooks (`user.created`, `user.updated`, `user.deleted`) at `/api/v1/webhooks/clerk`.

### Multi-Tenancy & Row-Level Security (PostgreSQL RLS)
- Defense-in-depth isolation: every request stashes `workspace_id` in `TenantContext`.
- Database transactions execute `SELECT set_config('app.current_workspace_id', :workspace_id, true)`.
- PostgreSQL RLS policies enforce `workspace_id = current_setting('app.current_workspace_id', true)::uuid`. Unauthenticated queries fail closed.
- ORM queries additionally filter by `workspace_id` for defense-in-depth.

### Billing & Monetization (Stripe)
- Hosted Stripe Checkout sessions (`POST /api/v1/billing/checkout`).
- Hosted Stripe Customer Portal sessions (`POST /api/v1/billing/portal`).
- Signature-verified webhooks (`POST /api/v1/webhooks/stripe`) for subscription lifecycle.

### Asynchronous Messaging & Background Workers (RabbitMQ)
- Topic exchange (`parabox.events`) with persistent queues.
- Asynchronous consumers for email notifications and domain events (`make dev-worker`).

### Multi-Provider Integrations Hub
- Native support for **GitHub**, **Slack**, **Linear**, and custom API keys/webhooks.
- Multi-tenant OAuth handshake with cryptographically signed, expiring HMAC-SHA256 state tokens to prevent CSRF.
- Sensitive tokens encrypted at rest via AES/Fernet encryption.
- Inbound webhook signature verification with automatic RabbitMQ event publication (`integrations.webhook.{provider}`).
- Row-Level Security policies on all integration credentials and workspace configuration tables.

### Realtime Synchronization (Socket.IO)
- Mounted at `/socket.io` on the FastAPI app.
- Backed by Redis Pub/Sub adapter to broadcast events across multiple server replicas.
- Automatically joins client sockets to rooms (`workspace:{workspace_id}`, `user:{user_id}`).

---

## 5. Developer Commands

| Command | Description |
| :--- | :--- |
| `make setup` | Install all dependencies with `uv` and copy `.env.example` |
| `make dev` | Start FastAPI server with hot-reload on `:8001` |
| `make dev-worker` | Run background event worker pool |
| `make test` | Run test suite with pytest (in-memory async DB) |
| `make lint` | Run ruff code linter |
| `make format` | Format code with ruff |
| `make typecheck` | Run mypy static type checking across all files |
| `make migrate` | Run Alembic database migrations |
| `make migration name="add_feature"` | Auto-generate a new Alembic migration revision |
| `make docker-up` | Start Postgres, Redis, RabbitMQ, MinIO containers |
| `make docker-down` | Stop local Docker containers |

---

## 6. How to Add a New Feature Context

To add a new business context (e.g. `src/contexts/documents/`):
1. **Domain:** Define entity in `src/contexts/documents/domain/entities.py` (inheriting from `BaseAggregateRoot` or `BaseEntity`).
2. **Infrastructure Model:** Create SQLAlchemy model in `src/contexts/documents/infrastructure/models.py` with `workspace_id` column.
3. **Repository:** Implement repository in `src/contexts/documents/infrastructure/repository.py`.
4. **Application Service:** Implement service in `src/contexts/documents/application/service.py` to record audit logs, dispatch domain events, or broadcast realtime updates.
5. **API Routes:** Mount endpoints in `src/adapters/api/routes/documents.py` using `Depends(get_active_tenant)` and `Depends(get_db)`.
6. **Register Router:** Include router in `src/bootstrap/api.py`.
7. **Migration:** Run `make migration name="add_documents_table"` and `make migrate`.
