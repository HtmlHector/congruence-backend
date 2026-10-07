"""FastMCP server definition exposing workspace and agent tools."""

from __future__ import annotations

import logging
from typing import Annotated, Any

from fastmcp import FastMCP
from pydantic import Field

logger = logging.getLogger(__name__)

mcp = FastMCP(
    "ParaboxAgentServer",
    instructions=(
        "Parabox Agent Server provides standard tool execution for AI agents, "
        "including workspace querying, task orchestration, and system diagnostics."
    ),
)


@mcp.tool(
    name="system_status",
    description="Check the status and health of the Parabox backend system.",
)
async def system_status() -> dict[str, Any]:
    return {
        "status": "healthy",
        "service": "parabox-backend-starter",
        "version": "0.1.0",
    }


@mcp.tool(
    name="summarize_text",
    description="Summarize given text or specification into concise markdown key points.",
)
async def summarize_text(
    text: Annotated[str, Field(description="Content to summarize")],
    max_points: Annotated[int, Field(description="Maximum bullet points", default=5)] = 5,
) -> dict[str, Any]:
    lines = [line.strip() for line in text.split("\n") if line.strip()]
    return {
        "summary": lines[:max_points],
        "total_lines_input": len(lines),
    }
