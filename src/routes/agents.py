"""Agent reasoning and tool execution endpoints."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastmcp.exceptions import NotFoundError
from pydantic import BaseModel, Field

from src.infrastructure.auth.dependencies import get_current_user
from src.infrastructure.auth.jwt import UserClaims
from src.infrastructure.llm.agent_gateway import (
    ProviderError,
    generate_completion,
    request_action,
)
from src.infrastructure.llm.fastmcp_server import mcp as fastmcp_server

router = APIRouter(prefix="/agents", tags=["agents"])


class ChatTurnRequest(BaseModel):
    messages: list[dict[str, Any]]
    model: str | None = None
    temperature: float = 0.0


class ActionRequest(BaseModel):
    messages: list[dict[str, Any]]
    tools: list[dict[str, Any]] = Field(default_factory=list)
    model: str | None = None


@router.post("/chat")
async def chat_turn(
    req: ChatTurnRequest,
    user: UserClaims = Depends(get_current_user),
) -> dict[str, Any]:
    """Execute a text completion turn through the LLM gateway."""
    try:
        content = await generate_completion(
            messages=req.messages,
            model=req.model,
            temperature=req.temperature,
        )
        return {"content": content}
    except ProviderError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.post("/action")
async def single_turn_action(
    req: ActionRequest,
    user: UserClaims = Depends(get_current_user),
) -> dict[str, Any]:
    """Ask model for exactly one tool call given running conversation and tools."""
    try:
        tool_name, tool_args, tool_call_id = await request_action(
            messages=req.messages,
            tools=req.tools,
            model=req.model,
        )
        return {
            "tool_name": tool_name,
            "tool_args": tool_args,
            "tool_call_id": tool_call_id,
        }
    except ProviderError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("/tools")
async def list_tools() -> list[dict[str, Any]]:
    """List available FastMCP tools exposed by the agent server."""
    tools = await fastmcp_server.list_tools()
    return [
        {
            "name": tool.name,
            "description": tool.description or "",
        }
        for tool in tools
    ]


class ExecuteToolRequest(BaseModel):
    tool_name: str
    tool_args: dict[str, Any] = Field(default_factory=dict)


@router.post("/execute-tool")
async def execute_tool(
    req: ExecuteToolRequest,
    user: UserClaims = Depends(get_current_user),
) -> dict[str, Any]:
    """Execute a registered FastMCP tool directly."""
    try:
        res = await fastmcp_server.call_tool(req.tool_name, req.tool_args)
        if res.structured_content is not None:
            return {"result": res.structured_content}
        text_content = [c.text for c in res.content if hasattr(c, "text")]
        return {
            "result": text_content
            if len(text_content) > 1
            else (text_content[0] if text_content else None)
        }
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=f"Tool '{req.tool_name}' not found") from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Tool execution failed: {exc}") from exc
