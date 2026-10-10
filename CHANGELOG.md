# Changelog

All notable changes to the `parabox-backend-starter` project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [0.1.0] - 2026-10-10

### Added
- **Core Framework & Runtime:** Python 3.12+ foundation managed with Astral `uv` and FastAPI.
- **Clean Architecture & DDD:** Domain, application, and infrastructure layering with decoupled aggregate roots and domain events.
- **Identity Context:** Clerk JWT authentication (JWKS verification), JIT user provisioning, and Svix webhook sync.
- **Workspace Context:** Multi-tenant workspace management, memberships, and RBAC roles (`owner`, `admin`, `member`).
- **Billing Context:** Stripe Customer integration, hosted Checkout sessions, Customer Portal, and subscription tier tracking.
- **Audit Context:** Immutable, append-only audit trail logging for security and compliance events.
- **Notification Context:** Transactional email delivery via Resend and asynchronous queue bus.
- **Sample Projects Context:** Golden reference CRUD context demonstrating tenant isolation, domain events, and controller routes.
- **Realtime Transport:** Socket.IO AsyncServer with Redis Pub/Sub adapter for horizontal scaling.
- **Message Bus:** RabbitMQ topic exchange topology via `aio-pika` with background worker consumer pool.
- **Object Storage:** S3 / MinIO client wrapper with presigned upload/download URLs.
- **Multi-Tenancy Security:** Defense-in-depth PostgreSQL Row-Level Security (RLS) enforcement via `app.current_workspace_id`.
- **Integrations Context:** Multi-Provider Integration Hub supporting GitHub, Slack, Linear, and custom API keys/webhooks, complete with AES/Fernet encryption at rest, PostgreSQL RLS, signed cryptographic OAuth state validation, and RabbitMQ event streaming.
- **Infrastructure & Tooling:** Multi-stage Dockerfiles (`api`, `worker`, `socketio`), Docker Compose for local infra, unified `Makefile`, and Alembic async migrations.
- **Repository Governance:** GitHub Actions CI, database migration check workflow, Dependabot, CODEOWNERS, PR/Issue templates, and pre-commit hooks.
