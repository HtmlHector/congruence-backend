# Parabox Backend Starter

A production-grade Python 3.12+ FastAPI backend service built to power products specified in the **Parabox Studio**.

It implements the 6 foundational primitives extracted from production-tested patterns, with PostgreSQL Row-Level Security (RLS) as the security boundary, unified LLM agent routing, and Stripe billing.

---

## 🏛 The 6 Foundational Primitives

1. **Identity & Authentication (`src/infrastructure/auth/`)**
   - Supabase Auth JWT verification (`HS256` / `RS256`).
   - Programmatic API Key hashing (`pb_live_...`) with SHA-256.
   - Standardized `UserClaims` dataclass.
   - FastAPI dependencies: `get_current_user`, `require_workspace_member`, `require_permission`.

2. **Workspace Tenancy & PostgreSQL RLS (`src/infrastructure/database/`)**
   - Transaction-scoped RLS propagation via `set_config('app.current_workspace_id', :workspace_id, true)`.
   - Fail-closed sentinel `00000000-0000-0000-0000-000000000000`.
   - PgBouncer / Supabase pooler compatibility with `statement_cache_size: 0` and `pool_pre_ping: true`.
   - Schema separation: `identity`, `workspace`, `subscription`, `audit`.

3. **Stripe Billing (`src/infrastructure/stripe/`)**
   - Server-side Checkout session creation.
   - Self-serve Customer Portal session creation.
   - Webhook signature verification and subscription lifecycle management.

4. **Agent Execution Gateway (`src/infrastructure/llm/`)**
   - Unified reasoning layer supporting OpenRouter, Anthropic, and OpenAI.
   - Bounded exponential backoff + jitter on 429 and 5xx errors.
   - Deterministic tool sorting for prompt caching.
   - Ephemeral prompt caching markers (`cache_control`).
   - FastMCP server (`ParaboxAgentServer`) for agent tool dispatch.

5. **Object Storage (`src/infrastructure/storage/`)**
   - S3-compatible client (AWS S3, Cloudflare R2, MinIO, Supabase Storage).
   - Presigned upload (PUT) and download (GET) URL generation.
   - Async non-blocking offload via `asyncio.to_thread`.

6. **Audit Logging & Compliance (`src/infrastructure/security/`)**
   - Append-only `audit.audit_log` records all resource mutations.
   - Isolated in PostgreSQL `begin_nested()` savepoints so compliance writes never abort user transactions.
   - Structured JSON logging via `structlog`.

---

## 🚀 Quickstart

### 1. Prerequisites
- Python 3.12+
- `uv` (recommended) or `pip`
- Docker and Docker Compose (for local Postgres & Redis)

### 2. Environment Setup
```bash
cp .env.example .env
```

### 3. Start Local Services
```bash
docker compose up -d
```

### 4. Install Dependencies
```bash
uv sync
```

### 5. Run Database Migrations
```bash
uv run alembic upgrade head
```

### 6. Start Development Server
```bash
uv run uvicorn src.main:app --reload --port 8000
```

Interactive API documentation will be available at:
- Swagger UI: [http://localhost:8000/docs](http://localhost:8000/docs)
- Health Check: [http://localhost:8000/health](http://localhost:8000/health)

### 7. Run Test Suite
```bash
uv run pytest -v
```

---

## 📂 Project Structure

```
├── alembic/                  # Database migration scripts & RLS policies
├── src/
│   ├── main.py               # Application composition root & lifespan
│   ├── config/               # Pydantic Settings & structlog logging
│   ├── contexts/             # Domain contexts (identity, workspace, billing, audit)
│   ├── infrastructure/
│   │   ├── auth/             # JWT decoding & RBAC dependencies
│   │   ├── database/         # Async engine, sessionmaker & RLS tenant scope
│   │   ├── llm/              # Unified LLM gateway & FastMCP server
│   │   ├── security/         # Transaction-safe AuditLogger
│   │   ├── storage/          # S3/R2 presigned URL client
│   │   └── stripe/           # Stripe API wrapper & webhook router
│   └── routes/               # FastAPI route modules (health, auth, workspaces, agents)
├── tests/                    # Async pytest suite
├── docker-compose.yml        # PostgreSQL 16 & Redis 7 services
└── pyproject.toml            # Project dependencies & tool configurations
```

---

## 🤖 Parabox Studio Integration

When spawned via `./bin/parabox build <slug> --backend`, this repository is cloned into `../<slug>-backend` and seeded with the **Six Canonical Documents** in `docs/`:
- `docs/prd.md` — Product Requirements Document
- `docs/trd.md` — Technical Requirements Document
- `docs/flow.md` — App Flow
- `docs/design.md` — UI/UX Design Brief
- `docs/schema.md` — Backend Schema & Domain Models
- `docs/plan.md` — 6-Milestone Implementation Plan

The `backend-engineer` subagent executes the plan milestones in strict sequence against this architecture.
