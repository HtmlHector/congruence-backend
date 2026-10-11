"""Health check and diagnostic endpoints."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.infrastructure.database.session import get_db

router = APIRouter(tags=["health"])


@router.get("/")
async def root() -> dict[str, str]:
    return {
        "service": "Parabox Backend",
        "status": "online",
        "docs": "/docs",
    }


@router.get("/health")
async def health_check(db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    db_status = "ok"
    try:
        await db.execute(text("SELECT 1"))
    except Exception as exc:
        db_status = f"unhealthy: {exc}"

    return {
        "status": "healthy" if db_status == "ok" else "degraded",
        "database": db_status,
        "version": "0.1.0",
    }


@router.get("/v1/models")
@router.get("/api/v1/models")
async def list_models() -> dict[str, Any]:
    """OpenAI-compatible models list endpoint for local AI tools and extensions."""
    models = [
        {"id": "claude-3-7-sonnet", "object": "model", "created": 1740000000, "owned_by": "anthropic"},
        {"id": "claude-3-5-sonnet", "object": "model", "created": 1729641600, "owned_by": "anthropic"},
        {"id": "gpt-4o", "object": "model", "created": 1715558400, "owned_by": "openai"},
        {"id": "o3-mini", "object": "model", "created": 1738000000, "owned_by": "openai"},
        {"id": "gemini-2.5-pro", "object": "model", "created": 1740000000, "owned_by": "google"},
        {"id": "gemini-2.5-flash", "object": "model", "created": 1740000000, "owned_by": "google"},
    ]
    return {"object": "list", "data": models}
