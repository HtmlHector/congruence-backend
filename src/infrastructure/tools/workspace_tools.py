"""Workspace Tool Suite for Congruence Direct Agent.

Provides sandboxed file reading, writing, directory listing, shell command execution,
and Git status/diff inspection strictly confined to the project worktree.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
from pathlib import Path
from typing import Any


def _resolve_safe_path(worktree_dir: str, relative_path: str) -> Path:
    """Resolve and validate that a target path is strictly contained within the worktree."""
    base = Path(worktree_dir).resolve()
    target = (base / relative_path).resolve()
    if not str(target).startswith(str(base)):
        raise PermissionError(f"Access denied: path '{relative_path}' is outside workspace boundary.")
    return target


async def execute_read_file(worktree_dir: str, file_path: str, offset: int = 1, limit: int = 300) -> str:
    """Read contents of a file with line numbers."""
    try:
        target = _resolve_safe_path(worktree_dir, file_path)
        if not target.exists():
            return f"Error: File not found at '{file_path}'"
        if target.is_dir():
            return f"Error: '{file_path}' is a directory, not a file."

        with open(target, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()

        total_lines = len(lines)
        start_idx = max(0, offset - 1)
        end_idx = min(total_lines, start_idx + limit)
        selected_lines = lines[start_idx:end_idx]

        output = [f"File: {file_path} (lines {start_idx + 1} to {end_idx} of {total_lines})"]
        for idx, line in enumerate(selected_lines, start=start_idx + 1):
            output.append(f"{idx:4d} | {line.rstrip()}")
        return "\n".join(output)
    except Exception as e:
        return f"Error reading file '{file_path}': {e}"


async def execute_write_file(worktree_dir: str, file_path: str, content: str) -> str:
    """Write or overwrite a file in the worktree."""
    try:
        target = _resolve_safe_path(worktree_dir, file_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        with open(target, "w", encoding="utf-8") as f:
            f.write(content)
        line_count = len(content.splitlines())
        return f"Successfully wrote {line_count} lines to '{file_path}'."
    except Exception as e:
        return f"Error writing file '{file_path}': {e}"


async def execute_edit_file(worktree_dir: str, file_path: str, old_text: str, new_text: str) -> str:
    """Replace an exact segment of text in an existing file."""
    try:
        target = _resolve_safe_path(worktree_dir, file_path)
        if not target.exists():
            return f"Error: File not found at '{file_path}'."
        with open(target, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
        if old_text not in content:
            # Fallback: check stripped whitespace match
            clean_old = old_text.strip()
            if clean_old in content:
                content = content.replace(clean_old, new_text.strip(), 1)
                with open(target, "w", encoding="utf-8") as f:
                    f.write(content)
                return f"Successfully edited '{file_path}'."
            return f"Error: Could not locate exact match for 'old_text' in '{file_path}'. Please verify line contents."
        new_content = content.replace(old_text, new_text, 1)
        with open(target, "w", encoding="utf-8") as f:
            f.write(new_content)
        return f"Successfully edited '{file_path}'."
    except Exception as e:
        return f"Error editing file '{file_path}': {e}"



async def execute_list_dir(worktree_dir: str, path: str = ".") -> str:
    """List entries in a workspace directory."""
    try:
        target = _resolve_safe_path(worktree_dir, path)
        if not target.exists():
            return f"Error: Directory not found at '{path}'"
        if not target.is_dir():
            return f"Error: '{path}' is a file, not a directory."

        entries = []
        for item in sorted(target.iterdir()):
            if item.name.startswith(".git") and item.name != ".gitignore":
                continue
            if item.is_dir():
                entries.append(f"📁 {item.name}/")
            else:
                size_kb = item.stat().st_size / 1024
                entries.append(f"📄 {item.name} ({size_kb:.1f} KB)")

        rel = str(target.relative_to(Path(worktree_dir).resolve()))
        header = f"Directory contents of '{rel if rel != '.' else '/'}':\n"
        return header + ("\n".join(entries) if entries else "(empty directory)")
    except Exception as e:
        return f"Error listing directory '{path}': {e}"


async def execute_run_command(worktree_dir: str, command: str, timeout_seconds: int = 45) -> str:
    """Execute a shell command inside the workspace directory."""
    try:
        proc = await asyncio.create_subprocess_shell(
            command,
            cwd=worktree_dir,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(), timeout=timeout_seconds
            )
        except asyncio.TimeoutError:
            try:
                proc.kill()
            except Exception:
                pass
            return f"Command timed out after {timeout_seconds} seconds: '{command}'"

        stdout = stdout_bytes.decode("utf-8", errors="replace").strip()
        stderr = stderr_bytes.decode("utf-8", errors="replace").strip()

        result = []
        if stdout:
            result.append(stdout)
        if stderr:
            result.append(f"[stderr]\n{stderr}")
        if not stdout and not stderr:
            result.append(f"(Command exited with code {proc.returncode})")
        return "\n".join(result)
    except Exception as e:
        return f"Error executing command '{command}': {e}"


async def execute_git_status(worktree_dir: str) -> str:
    """Inspect Git worktree status."""
    return await execute_run_command(worktree_dir, "git status -s")


async def execute_git_diff(worktree_dir: str) -> str:
    """Inspect uncommitted Git diffs."""
    return await execute_run_command(worktree_dir, "git diff HEAD")


# Tool definitions formatted for model function calling
WORKSPACE_TOOLS_SCHEMA = [
    {
        "name": "read_file",
        "description": "Read the contents of a file in the workspace repository with line numbers.",
        "parameters": {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "Relative path to the file to read (e.g. 'src/main.py', 'package.json').",
                },
                "offset": {
                    "type": "integer",
                    "description": "Optional starting line number (1-indexed, default 1).",
                },
                "limit": {
                    "type": "integer",
                    "description": "Optional number of lines to read (default 300).",
                },
            },
            "required": ["file_path"],
        },
    },
    {
        "name": "edit_file",
        "description": "Replace an exact segment of text (old_text) with new_text in an existing file. Read the file first to ensure old_text matches exactly.",
        "parameters": {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "Relative path to the file to edit (e.g. 'src/App.tsx').",
                },
                "old_text": {
                    "type": "string",
                    "description": "The exact existing text chunk to find and replace.",
                },
                "new_text": {
                    "type": "string",
                    "description": "The new text chunk to replace old_text with.",
                },
            },
            "required": ["file_path", "old_text", "new_text"],
        },
    },
    {
        "name": "write_file",
        "description": "Write or overwrite a file in the workspace with new content.",
        "parameters": {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "Relative path to the file to write (e.g. 'src/utils.py').",
                },
                "content": {
                    "type": "string",
                    "description": "The complete text content to write to the file.",
                },
            },
            "required": ["file_path", "content"],
        },
    },
    {
        "name": "list_dir",
        "description": "List files and subdirectories in a directory of the workspace.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Relative directory path to list (default '.' for workspace root).",
                },
            },
        },
    },
    {
        "name": "run_command",
        "description": "Run a shell command (e.g. test runner, linter, git command, build command) inside the workspace.",
        "parameters": {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "The shell command to execute.",
                },
            },
            "required": ["command"],
        },
    },
    {
        "name": "git_status",
        "description": "Check the current Git status of modified, untracked, and staged files.",
        "parameters": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "git_diff",
        "description": "View uncommitted Git diff changes in the workspace.",
        "parameters": {
            "type": "object",
            "properties": {},
        },
    },
]


async def dispatch_tool_call(worktree_dir: str, tool_name: str, tool_args: dict[str, Any]) -> str:
    """Dispatch a tool call by name with validated arguments."""
    if tool_name == "read_file":
        return await execute_read_file(
            worktree_dir,
            file_path=tool_args.get("file_path", ""),
            offset=tool_args.get("offset", 1),
            limit=tool_args.get("limit", 300),
        )
    elif tool_name == "edit_file":
        return await execute_edit_file(
            worktree_dir,
            file_path=tool_args.get("file_path", ""),
            old_text=tool_args.get("old_text", ""),
            new_text=tool_args.get("new_text", ""),
        )
    elif tool_name == "write_file":
        return await execute_write_file(
            worktree_dir,
            file_path=tool_args.get("file_path", ""),
            content=tool_args.get("content", ""),
        )
    elif tool_name == "list_dir":
        return await execute_list_dir(
            worktree_dir,
            path=tool_args.get("path", "."),
        )
    elif tool_name == "run_command":
        return await execute_run_command(
            worktree_dir,
            command=tool_args.get("command", ""),
        )
    elif tool_name == "git_status":
        return await execute_git_status(worktree_dir)
    elif tool_name == "git_diff":
        return await execute_git_diff(worktree_dir)
    else:
        return f"Unknown tool: '{tool_name}'"
