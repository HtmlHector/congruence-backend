"""Local Host-Custody Agent Runner for Antigravity, Claude Code, Codex, and Shell.

Ensures all agents are capped strictly to their respective project repositories and Git worktrees.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
from typing import AsyncGenerator


def _find_binary(name: str) -> str | None:
    # Common local paths + standard PATH
    search_paths = [
        os.path.expanduser("~/.local/bin"),
        os.path.expanduser("~/bin"),
        "/opt/homebrew/bin",
        "/usr/local/bin",
        "/usr/bin",
        "/bin",
    ]
    for p in search_paths:
        candidate = os.path.join(p, name)
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    return shutil.which(name)


AGY_MODEL_MAP = {
    "gemini 3.8 flash": "gemini-3.8-flash-high",
    "gemini 3.8 flash (high)": "gemini-3.8-flash-high",
    "gemini 3.8 flash (medium)": "gemini-3.8-flash-medium",
    "gemini 3.8 flash (low)": "gemini-3.8-flash-low",
    "gemini 3.7 flash": "gemini-3.7-flash-high",
    "gemini 3.7 flash (high)": "gemini-3.7-flash-high",
    "gemini 3.6 flash": "gemini-3.6-flash-high",
    "gemini 3.1 pro": "gemini-3.1-pro-high",
    "gemini 3.1 pro (high)": "gemini-3.1-pro-high",
    "claude sonnet 4.6": "claude-sonnet-4-6",
    "claude sonnet 4.6 (thinking)": "claude-sonnet-4-6",
    "claude opus 4.6": "claude-opus-4-6-thinking",
    "claude opus 4.6 (thinking)": "claude-opus-4-6-thinking",
    "gpt-oss 120b": "gpt-oss-120b-medium",
    "gpt-oss 120b (medium)": "gpt-oss-120b-medium",
}


CLAUDE_MODEL_MAP = {
    "claude sonnet 4.6": "claude-sonnet-4-6",
    "claude sonnet 4.6 (thinking)": "claude-sonnet-4-6",
    "claude 3.7 sonnet": "claude-3-7-sonnet-20250219",
    "claude 3.7 sonnet (thinking)": "claude-3-7-sonnet-20250219",
    "claude 3.5 sonnet": "claude-3-5-sonnet-20241022",
    "claude 3.5 haiku": "claude-3-5-haiku-20241022",
    "claude opus 4.6": "claude-opus-4-6-thinking",
    "claude opus 4.6 (thinking)": "claude-opus-4-6-thinking",
}

CODEX_MODEL_MAP = {
    "o3-mini": "gpt-oss-120b-medium",
    "gpt-4o": "gpt-oss-120b-medium",
    "gpt-oss 120b": "gpt-oss-120b-medium",
    "gpt-oss 120b (medium)": "gpt-oss-120b-medium",
}

_chat_conversation_map: dict[str, str] = {}


async def _ensure_project_repo(project_name: str, branch: str = "main") -> str:
    """Ensure project directory exists on disk with real files, initialized git,
    and isolated branch worktree.
    """
    clean_slug = project_name.lower().replace(" ", "-").replace("/", "-")
    if "ecommerce" in clean_slug:
        clean_slug = "ecommerce-test-app"
    elif "spec-doc" in clean_slug:
        clean_slug = "spec-docs"
    elif "backend" in clean_slug:
        clean_slug = "congruence-backend"
    elif "frontend" in clean_slug:
        clean_slug = "congruence-frontend"

    base_dir = os.path.expanduser(f"~/.congruence/projects/{clean_slug}")
    os.makedirs(base_dir, exist_ok=True)

    # Seed AGENTS.md and CLAUDE.md workspace instructions if not already present
    agents_md_path = os.path.join(base_dir, "AGENTS.md")
    if not os.path.exists(agents_md_path):
        with open(agents_md_path, "w", encoding="utf-8") as f:
            f.write(
                f"# Congruence Workspace · {project_name}\n\n"
                "Welcome to your Congruence agent workspace.\n\n"
                "## Operating Guidelines\n"
                "- You are a helpful, expert AI pair programming agent operating in this project repository.\n"
                "- Always inspect files and existing project structure first before making changes.\n"
                "- Provide clickable GitHub markdown file links `[filename](file:///path/to/file#L1-L20)`.\n"
                "- When executing tasks, explain your reasoning concisely and verify your changes with tests or dev server checks.\n"
            )

    claude_md_path = os.path.join(base_dir, "CLAUDE.md")
    if not os.path.exists(claude_md_path):
        with open(claude_md_path, "w", encoding="utf-8") as f:
            f.write(
                f"# Congruence Project Guidelines · {project_name}\n\n"
                "## Guidelines for Claude Code\n"
                "- Inspect existing codebase structure before proposing or applying code modifications.\n"
                "- Make precision edits and run tests or dev servers to verify fixes.\n"
                "- Reference files with clear relative paths.\n"
            )

    git_dir = os.path.join(base_dir, ".git")
    if not os.path.exists(git_dir):
        # Initialize repository structure
        if clean_slug == "ecommerce-test-app":
            pkg_json = {
                "name": "ecommerce-test-app",
                "version": "1.0.0",
                "private": True,
                "scripts": {
                    "dev": "next dev --port 3000",
                    "build": "next build",
                    "start": "next start",
                    "test": "vitest run",
                },
                "dependencies": {
                    "next": "15.5.27",
                    "react": "^19.0.0",
                    "react-dom": "^19.0.0",
                    "lucide-react": "^1.16.0",
                    "tailwindcss": "^4.0.0",
                    "stripe": "^17.0.0",
                },
            }
            with open(os.path.join(base_dir, "package.json"), "w", encoding="utf-8") as f:
                json.dump(pkg_json, f, indent=2)

            with open(os.path.join(base_dir, "README.md"), "w", encoding="utf-8") as f:
                f.write(
                    "# Ecommerce Test App\n\n"
                    "A Next.js 15 e-commerce storefront app with real-time product catalogs, "
                    "shopping cart, and Stripe checkout.\n\n"
                    "## Getting Started\n"
                    "Run `npm run dev` to launch the local dev server on port 3000.\n"
                )

            src_app = os.path.join(base_dir, "src", "app")
            os.makedirs(src_app, exist_ok=True)
            with open(os.path.join(src_app, "page.tsx"), "w", encoding="utf-8") as f:
                f.write(
                    'export default function StorefrontPage() {\n'
                    '  return (\n'
                    '    <main className="min-h-screen p-8 bg-zinc-50 font-sans">\n'
                    '      <h1 className="text-2xl font-bold">Ecommerce Storefront</h1>\n'
                    '      <p className="text-sm text-zinc-600 mt-2">Active product catalog & live preview.</p>\n'
                    '    </main>\n'
                    '  );\n'
                    '}\n'
                )

        elif clean_slug == "spec-docs":
            with open(os.path.join(base_dir, "README.md"), "w", encoding="utf-8") as f:
                f.write(
                    "# Spec Docs\n\n"
                    "System architectural specifications and API contract documentation.\n"
                )
            docs_dir = os.path.join(base_dir, "docs")
            os.makedirs(docs_dir, exist_ok=True)
            with open(os.path.join(docs_dir, "architecture.md"), "w", encoding="utf-8") as f:
                f.write("# Congruence Architecture & Invariants\n")

        # Git init
        proc = await asyncio.create_subprocess_exec(
            "git", "init", "-b", "main", base_dir,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await proc.communicate()

        proc2 = await asyncio.create_subprocess_exec(
            "git", "add", ".",
            cwd=base_dir,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await proc2.communicate()

        proc3 = await asyncio.create_subprocess_exec(
            "git", "-c", "user.name=Congruence", "-c", "user.email=bot@congruence.dev",
            "commit", "-m", "Initial project workspace commit",
            cwd=base_dir,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await proc3.communicate()

    # If on a specific worktree branch other than main
    if branch and branch != "main":
        safe_branch = branch.replace("/", "-")
        worktree_dir = os.path.join(base_dir, "worktrees", safe_branch)
        if not os.path.exists(worktree_dir):
            os.makedirs(os.path.dirname(worktree_dir), exist_ok=True)
            wt_proc = await asyncio.create_subprocess_exec(
                "git", "worktree", "add", "-b", branch, worktree_dir,
                cwd=base_dir,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await wt_proc.communicate()
            if wt_proc.returncode != 0:
                # Branch might already exist
                wt_proc2 = await asyncio.create_subprocess_exec(
                    "git", "worktree", "add", worktree_dir, branch,
                    cwd=base_dir,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                await wt_proc2.communicate()
        if os.path.exists(worktree_dir):
            return worktree_dir

    return base_dir


async def run_agent_stream(
    harness: str,
    prompt: str,
    cwd: str | None = None,
    model: str | None = None,
    project_id: str | None = None,
    lane_id: str | None = None,
    branch: str | None = None,
    chat_id: str | None = None,
) -> AsyncGenerator[dict, None]:
    """Execute host-native CLI agent (Antigravity, Claude Code, Codex, Shell)
    capped strictly to the project repository workspace with multi-turn session persistence.
    """
    import uuid

    # Resolve project repository directory
    if cwd and os.path.exists(cwd):
        working_dir = cwd
    else:
        project_name = project_id or "ecommerce-test-app"
        working_dir = await _ensure_project_repo(project_name, branch=branch or "main")

    harness_normalized = harness.lower()
    is_agy = "antigravity" in harness_normalized or "gemini" in harness_normalized
    is_claude = "claude" in harness_normalized or "anthropic" in harness_normalized
    is_codex = "codex" in harness_normalized or "openai" in harness_normalized

    if is_agy:
        bin_path = _find_binary("agy")
        if not bin_path:
            yield {
                "type": "error",
                "message": "Antigravity CLI ('agy') not found in host PATH. Please run 'curl -fsSL https://antigravity.dev/install.sh | bash' or install agy.",
            }
            return

        cmd = [
            bin_path,
            "--add-dir", working_dir,
            "--output-format", "stream-json",
            "--dangerously-skip-permissions",
        ]
        
        # Multi-turn conversation persistence
        conv_id_to_use = _chat_conversation_map.get(chat_id) if chat_id else None
        if conv_id_to_use:
            cmd.extend(["--conversation", conv_id_to_use])

        if model:
            cleaned_m = model.lower().strip()
            mapped_model = AGY_MODEL_MAP.get(cleaned_m) or (model if "-" in model else None)
            if mapped_model:
                cmd.extend(["--model", mapped_model])

        cmd.extend(["-p", prompt])

    elif is_claude:
        bin_path = _find_binary("claude")
        if not bin_path:
            yield {
                "type": "error",
                "message": "Claude Code CLI ('claude') not found in host PATH. Please install with 'npm install -g @anthropic-ai/claude-code'.",
            }
            return

        cmd = [
            bin_path,
            "--verbose",
            "--dangerously-skip-permissions",
            "--output-format", "stream-json",
        ]

        # Multi-turn session persistence for Claude
        claude_session = _chat_conversation_map.get(chat_id)
        if not claude_session and chat_id:
            claude_session = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"claude-{chat_id}"))
            _chat_conversation_map[chat_id] = claude_session

        if claude_session:
            cmd.extend(["--session-id", claude_session])

        if model:
            cleaned_m = model.lower().strip()
            mapped_model = CLAUDE_MODEL_MAP.get(cleaned_m) or (model if "-" in model else None)
            if mapped_model:
                cmd.extend(["--model", mapped_model])

        cmd.extend(["-p", prompt])

    elif is_codex:
        bin_path = _find_binary("codex")
        if bin_path:
            cmd = [bin_path, "exec", prompt]
        else:
            # Fallback to agy with Codex / OpenAI model for full multi-turn memory & tool support
            bin_path = _find_binary("agy")
            if not bin_path:
                yield {
                    "type": "error",
                    "message": "Neither Codex CLI nor Antigravity CLI found on host PATH.",
                }
                return

            cmd = [
                bin_path,
                "--add-dir", working_dir,
                "--output-format", "stream-json",
                "--dangerously-skip-permissions",
            ]
            conv_id_to_use = _chat_conversation_map.get(chat_id) if chat_id else None
            if conv_id_to_use:
                cmd.extend(["--conversation", conv_id_to_use])

            codex_model = "gpt-oss-120b-medium"
            if model:
                cleaned_m = model.lower().strip()
                codex_model = CODEX_MODEL_MAP.get(cleaned_m, codex_model)

            cmd.extend(["--model", codex_model, "-p", prompt])

    else:
        # Default Shell
        cmd = ["/bin/sh", "-c", prompt]

    yield {
        "type": "status",
        "harness": harness,
        "cwd": working_dir,
        "command": " ".join(cmd),
    }

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=working_dir,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env={
                **os.environ,
                "PYTHONUNBUFFERED": "1",
                "NODE_NO_WARNINGS": "1",
                "FORCE_COLOR": "0",
                "NO_COLOR": "1",
            },
        )

        accumulated_text = []
        queue = asyncio.Queue()

        async def stream_pump(stream, stream_name: str):
            try:
                while True:
                    line = await stream.readline()
                    if not line:
                        break
                    await queue.put((stream_name, line))
            except Exception as e:
                await queue.put(("error", str(e).encode()))
            finally:
                await queue.put((stream_name, None))

        # Run stdout and stderr pumps concurrently
        t_out = asyncio.create_task(stream_pump(proc.stdout, "stdout"))
        t_err = asyncio.create_task(stream_pump(proc.stderr, "stderr"))

        closed_streams = 0
        while closed_streams < 2:
            try:
                stream_name, raw_line = await asyncio.wait_for(queue.get(), timeout=0.1)
            except asyncio.TimeoutError:
                if proc.returncode is not None and closed_streams >= 2:
                    break
                continue

            if raw_line is None:
                closed_streams += 1
                continue

            decoded = raw_line.decode("utf-8", errors="replace").strip()
            if not decoded:
                continue

            if stream_name == "stdout":
                try:
                    parsed = json.loads(decoded)

                    # 1. Antigravity Stream-JSON Format
                    if "event" in parsed:
                        ev = parsed.get("event")

                        if ev == "init":
                            conv_id = parsed.get("conversation_id")
                            if conv_id and chat_id:
                                _chat_conversation_map[chat_id] = conv_id
                            yield {
                                "type": "init",
                                "conversation_id": conv_id,
                            }

                        elif ev == "step_update":
                            step_up = parsed.get("step_update", {})
                            step_type = step_up.get("step_type")
                            state = step_up.get("state")
                            delta = step_up.get("text_delta")

                            # Handle Tool execution events from Antigravity CLI
                            if step_type == "tool":
                                t_name = step_up.get("tool_name") or "tool"
                                t_info = step_up.get("tool_info", {})
                                t_params = t_info.get("parameters") or t_info.get("args") or {}
                                t_params_str = json.dumps(t_params) if isinstance(t_params, dict) else str(t_params)

                                if state == "ACTIVE":
                                    yield {
                                        "type": "tool_start",
                                        "tool": t_name,
                                        "params": t_params_str,
                                        "status": "running",
                                    }
                                    if "ask_question" in t_name.lower() or "permission" in t_name.lower():
                                        q_text = str(t_params.get("question", t_params)) if isinstance(t_params, dict) else str(t_params)
                                        opts = t_params.get("options", []) if isinstance(t_params, dict) else []
                                        yield {
                                            "type": "question",
                                            "question": q_text,
                                            "options": opts,
                                        }
                                elif state == "DONE":
                                    yield {
                                        "type": "tool_done",
                                        "tool": t_name,
                                        "params": t_params_str,
                                        "output": str(t_info.get("output", "")),
                                        "duration_seconds": step_up.get("duration_seconds", 0),
                                    }

                            elif step_type in ("thought", "planning", "reasoning"):
                                thinking_delta = (
                                    step_up.get("thinking_delta")
                                    or step_up.get("thought")
                                    or step_up.get("thinking")
                                    or delta
                                )
                                if thinking_delta:
                                    yield {"type": "thinking", "text": str(thinking_delta)}

                            elif step_type == "agent_response":
                                thinking_delta = (
                                    step_up.get("thinking_delta")
                                    or step_up.get("thought")
                                    or step_up.get("thinking")
                                )
                                if thinking_delta:
                                    yield {"type": "thinking", "text": str(thinking_delta)}
                                if delta:
                                    accumulated_text.append(delta)
                                    yield {"type": "chunk", "text": delta, "stream": stream_name}

                            elif delta:
                                accumulated_text.append(delta)
                                yield {"type": "chunk", "text": delta, "stream": stream_name}

                        elif ev == "result":
                            res_obj = parsed.get("result", {})
                            conv_id = res_obj.get("conversation_id")
                            if conv_id and chat_id:
                                _chat_conversation_map[chat_id] = conv_id
                            res_text = res_obj.get("response", "")
                            duration = res_obj.get("duration_seconds", 0)
                            usage = res_obj.get("usage", {})
                            if res_text and not accumulated_text:
                                accumulated_text.append(res_text)
                                yield {"type": "chunk", "text": res_text, "stream": stream_name}
                            yield {
                                "type": "metrics",
                                "duration_seconds": duration,
                                "usage": usage,
                            }
                        continue

                    # 2. Claude Code Stream-JSON Format
                    if "type" in parsed:
                        p_type = parsed.get("type")

                        if p_type == "system" and parsed.get("subtype") == "init":
                            s_id = parsed.get("session_id")
                            if s_id and chat_id:
                                _chat_conversation_map[chat_id] = s_id
                            yield {"type": "init", "conversation_id": s_id}
                            continue

                        elif p_type == "assistant":
                            msg_obj = parsed.get("message", {})
                            for item in msg_obj.get("content", []):
                                if item.get("type") == "text":
                                    t_text = item.get("text", "")
                                    accumulated_text.append(t_text)
                                    yield {"type": "chunk", "text": t_text, "stream": stream_name}
                                elif item.get("type") == "thinking":
                                    th_text = item.get("thinking", "")
                                    if th_text:
                                        yield {"type": "thinking", "text": th_text}
                                elif item.get("type") == "tool_use":
                                    t_name = item.get("name", "tool")
                                    t_input = item.get("input", {})
                                    yield {
                                        "type": "tool_start",
                                        "tool": t_name,
                                        "params": json.dumps(t_input) if isinstance(t_input, dict) else str(t_input),
                                        "status": "running",
                                    }
                            continue

                        elif p_type == "result":
                            res_val = parsed.get("result", "")
                            dur_sec = round((parsed.get("duration_ms", 0) / 1000), 2)
                            if res_val and not accumulated_text:
                                accumulated_text.append(str(res_val))
                                yield {"type": "chunk", "text": str(res_val), "stream": stream_name}
                            yield {
                                "type": "metrics",
                                "duration_seconds": dur_sec,
                                "usage": parsed.get("usage", {}),
                            }
                            continue

                except json.JSONDecodeError:
                    pass

            # Check for XML/Markdown thinking tags in standard output stream
            if "<thinking>" in decoded and "</thinking>" in decoded:
                start_idx = decoded.find("<thinking>") + len("<thinking>")
                end_idx = decoded.find("</thinking>")
                thought_text = decoded[start_idx:end_idx].strip()
                remainder = (decoded[:decoded.find("<thinking>")] + decoded[end_idx + len("</thinking>"):])
                if thought_text:
                    yield {"type": "thinking", "text": thought_text + "\n"}
                if remainder.strip():
                    accumulated_text.append(remainder + "\n")
                    yield {"type": "chunk", "text": remainder + "\n", "stream": stream_name}
                continue

            # Fallback for standard stdout/stderr
            accumulated_text.append(decoded + "\n")
            yield {"type": "chunk", "text": decoded + "\n", "stream": stream_name}

        await asyncio.gather(t_out, t_err, return_exceptions=True)
        returncode = await proc.wait()

        yield {
            "type": "done",
            "exit_code": returncode,
            "success": returncode == 0,
            "full_text": "".join(accumulated_text),
        }

    except Exception as exc:
        yield {
            "type": "error",
            "message": f"Execution failed: {exc}",
        }
