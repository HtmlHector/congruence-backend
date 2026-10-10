# Release Process

This document describes the versioning strategy, pre-release verification, and release workflow for `parabox-backend-starter`.

---

## 1. Versioning Strategy

This project adheres to [Semantic Versioning (SemVer)](https://semver.org/):
- **MAJOR (X.0.0):** Incompatible architectural changes, database breaking changes, or major dependency replacements.
- **MINOR (0.X.0):** New primitives, context additions, or backward-compatible feature expansions.
- **PATCH (0.0.X):** Bug fixes, performance improvements, and documentation/CI updates.

---

## 2. Release Checklist

Before releasing a new version:

1. **Verify Quality Gates:**
   - [ ] All tests passing: `make test`
   - [ ] Clean typecheck: `make typecheck`
   - [ ] Clean linting: `make lint`
   - [ ] Docker builds cleanly: `docker compose build`
2. **Database Integrity:**
   - [ ] Ensure only a single Alembic head revision exists: `uv run alembic heads`
   - [ ] Migrations upgrade and downgrade cleanly without errors.
3. **Update Documentation:**
   - [ ] Bump version in `pyproject.toml` and `src/__init__.py`.
   - [ ] Document changes in `CHANGELOG.md` under a new version heading.

---

## 3. Creating a Release

1. **Commit and Tag:**
   ```bash
   git add pyproject.toml src/__init__.py CHANGELOG.md
   git commit -m "chore(release): bump version to v0.1.0"
   git tag -a v0.1.0 -m "Release v0.1.0"
   git push origin main --tags
   ```

2. **Publish Container Images (if configured):**
   - CI builds and publishes tagged container images (`api`, `worker`, `socketio`) to GitHub Container Registry (`ghcr.io`).

---

## 4. Rollback Procedure

In case of a critical failure:
1. Revert the release commit or roll back container deployments to the previous tag.
2. If schema changes were applied, run `uv run alembic downgrade <target_revision>`.
