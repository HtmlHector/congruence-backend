"""Internal tool registry endpoints.

The ``/chat`` and ``/action`` endpoints that used to sit here have been removed.
They called the LLM gateway, which spent *our* OpenAI/OpenRouter keys to run an
agent loop for the user. That is inference resale, and it contradicted
Harness Neutrality (``prd.md`` Invariant 3) and Host Custody (Invariant 4).

These remaining endpoints only introspect and invoke locally-registered tools.
They perform no model call and hold no credential.
"""

from __future__ import annotations

import os
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastmcp.exceptions import NotFoundError
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from src.infrastructure.auth.dependencies import get_current_user
from src.infrastructure.auth.jwt import UserClaims
from src.infrastructure.auth.tenancy import lane_access, require_project_access
from src.infrastructure.database.session import get_db_session
from src.infrastructure.llm.fastmcp_server import mcp as fastmcp_server

router = APIRouter(prefix="/agents", tags=["agents"])


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


class AgentChatStreamRequest(BaseModel):
    prompt: str
    harness: str = "Antigravity"
    model: str | None = None
    project_id: str | None = None
    lane_id: str | None = None
    branch: str | None = None
    cwd: str | None = None
    chat_id: str | None = None


@router.post("/chat/stream")
async def stream_agent_chat(
    req: AgentChatStreamRequest,
    user: UserClaims = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
):
    """Execute host-custody agent CLI (Antigravity, Claude Code, Codex, Shell)
    and stream SSE events directly to the frontend chat UI.

    Tenancy: the agent may only target projects/lanes the caller can access,
    and ``cwd`` must stay inside the runner's project workspace root.
    """
    import json

    from fastapi.responses import StreamingResponse

    from src.infrastructure.runners.agent_runner import (
        project_workspace_dir,
        run_agent_stream,
    )

    if req.project_id:
        await require_project_access(db, user, req.project_id)
    if req.lane_id:
        access = await lane_access(db, user, req.lane_id)
        if access is False:
            raise HTTPException(status_code=404, detail="Lane not found")
    if req.cwd:
        if not req.project_id:
            raise HTTPException(
                status_code=403, detail="cwd requires an authorized project_id"
            )
        project_root = os.path.realpath(project_workspace_dir(req.project_id))
        real_cwd = os.path.realpath(os.path.expanduser(req.cwd))
        if real_cwd != project_root and not real_cwd.startswith(project_root + os.sep):
            raise HTTPException(
                status_code=400,
                detail="cwd must be inside the authorized project workspace",
            )

    async def event_generator():
        async for event in run_agent_stream(
            harness=req.harness,
            prompt=req.prompt,
            cwd=req.cwd,
            model=req.model,
            project_id=req.project_id,
            lane_id=req.lane_id,
            branch=req.branch,
            chat_id=req.chat_id,
        ):
            yield f"data: {json.dumps(event)}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


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

