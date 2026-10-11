"""Direct API Agent execution loop for Congruence.

Routes user prompts directly to Google Gemini, Anthropic Claude, or OpenAI APIs using
decrypted Secrets Vault credentials, with full workspace tool execution and real-time SSE streaming.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, AsyncGenerator

import httpx

from src.infrastructure.tools.workspace_tools import (
    WORKSPACE_TOOLS_SCHEMA,
    dispatch_tool_call,
)


def _read_cli_auth(harness_type: str, env: dict[str, str] | None = None) -> str | None:
    """Read credentials from CLI auth files in $HOME if available."""
    if env and env.get("DISABLE_CLI_AUTH") == "1":
        return None
    if os.environ.get("DISABLE_CLI_AUTH") == "1" or os.environ.get("PYTEST_CURRENT_TEST"):
        return None
    home = Path.home()
    if harness_type in ("gemini", "antigravity"):
        for p in [
            home / ".gemini" / "antigravity-cli" / "auth.json",
            home / ".gemini" / "auth.json",
            home / ".antigravity" / "auth.json",
        ]:
            if p.exists():
                try:
                    data = json.loads(p.read_text(encoding="utf-8"))
                    key = data.get("api_key") or data.get("token") or data.get("access_token")
                    if key and not str(key).startswith(("google_sandbox_", "agy_congruence_")):
                        return str(key).strip()
                except Exception:
                    pass
    elif harness_type in ("claude", "anthropic"):
        for p in [
            home / ".claude.json",
            home / ".claude" / "config.json",
            home / ".anthropic" / "config.json",
        ]:
            if p.exists():
                try:
                    data = json.loads(p.read_text(encoding="utf-8"))
                    key = data.get("apiKey") or data.get("api_key") or data.get("primaryApiKey")
                    if key:
                        return str(key).strip()
                except Exception:
                    pass
    elif harness_type in ("openai", "codex"):
        for p in [
            home / ".codex" / "auth.json",
            home / ".openai" / "auth.json",
        ]:
            if p.exists():
                try:
                    data = json.loads(p.read_text(encoding="utf-8"))
                    key = data.get("token") or data.get("api_key")
                    if key and not str(key).startswith("sk-codex-sess-"):
                        return str(key).strip()
                except Exception:
                    pass
    elif harness_type in ("opencode", "openrouter"):
        for p in [
            home / ".local" / "share" / "opencode" / "auth.json",
            home / ".config" / "opencode" / "auth.json",
        ]:
            if p.exists():
                try:
                    data = json.loads(p.read_text(encoding="utf-8"))
                    key = data.get("token") or data.get("api_key")
                    if key:
                        return str(key).strip()
                except Exception:
                    pass
    return None


def _map_gemini_model_name(raw_model: str | None) -> str:
    """Map frontend model dropdown string to Google Gemini model identifier."""
    if not raw_model:
        return "gemini-2.5-flash"
    m = raw_model.lower().strip()
    if "pro" in m:
        return "gemini-2.5-pro"
    if "2.0" in m:
        return "gemini-2.0-flash"
    if "1.5-pro" in m:
        return "gemini-1.5-pro"
    if "1.5" in m:
        return "gemini-1.5-flash"
    return "gemini-2.5-flash"


def _map_claude_model_name(raw_model: str | None) -> str:
    """Map frontend model dropdown string to Anthropic Claude model identifier."""
    if not raw_model:
        return "claude-3-7-sonnet-20250219"
    m = raw_model.lower().strip()
    if "3.7" in m or "4.6" in m:
        return "claude-3-7-sonnet-20250219"
    if "haiku" in m:
        return "claude-3-5-haiku-20241022"
    if "sonnet" in m:
        return "claude-3-5-sonnet-20241022"
    return "claude-3-7-sonnet-20250219"


def _map_openai_model_name(raw_model: str | None) -> str:
    """Map frontend model dropdown string to OpenAI / OpenRouter model identifier."""
    if not raw_model:
        return "gpt-4o"
    if "/" in raw_model or ":" in raw_model:
        return raw_model.strip()
    m = raw_model.lower().strip()
    if "4o-mini" in m or "mini" in m:
        return "gpt-4o-mini"
    if "o3" in m:
        return "o3-mini"
    if "4o" in m:
        return "gpt-4o"
    return raw_model.strip()


# In-memory multi-turn session history per chat_id
_chat_histories: dict[str, list[dict[str, Any]]] = {}


async def run_direct_agent_stream(
    harness: str,
    prompt: str,
    working_dir: str,
    model: str | None = None,
    chat_id: str | None = None,
    extra_env: dict[str, str] | None = None,
) -> AsyncGenerator[dict[str, Any], None]:
    """Execute direct API agent with multi-turn conversation and workspace tools."""
    env = {**os.environ, **(extra_env or {})}
    harness_norm = (harness or "antigravity").lower().strip()

    if harness_norm == "shell":
        yield {
            "type": "status",
            "harness": "Shell",
            "model": "bash",
            "cwd": working_dir,
        }
        output = await dispatch_tool_call(working_dir, "run_command", {"command": prompt})
        yield {"type": "chunk", "text": output}
        yield {"type": "done", "text": output}
        return

    is_gemini = "antigravity" in harness_norm or "gemini" in harness_norm or "google" in harness_norm
    is_claude = "claude" in harness_norm or "anthropic" in harness_norm
    is_openai = "codex" in harness_norm or "openai" in harness_norm or "gpt" in harness_norm
    is_opencode = "opencode" in harness_norm
    is_commandcode = "command" in harness_norm or "cmd" in harness_norm or "taste" in harness_norm
    is_openrouter = "openrouter" in harness_norm or "router" in harness_norm

    # 1. Resolve Provider API Key
    api_key: str | None = None
    provider_label = "Google Antigravity"
    key_env_var = "GEMINI_API_KEY"

    if is_openrouter:
        api_key = (
            env.get("OPENROUTER_API_KEY")
            or env.get("OPENROUTER_KEY")
            or env.get("OPENROUTER_TOKEN")
            or env.get("GEMINI_API_KEY")
            or env.get("ANTHROPIC_API_KEY")
            or env.get("OPENAI_API_KEY")
        )
        provider_label = "OpenRouter Gateway"
        key_env_var = "OPENROUTER_API_KEY"
        if env.get("GEMINI_API_KEY") and not (
            env.get("OPENROUTER_API_KEY")
            or env.get("OPENROUTER_KEY")
            or env.get("OPENROUTER_TOKEN")
        ):
            is_gemini = True
        else:
            is_openai = True
    elif is_commandcode:
        api_key = (
            env.get("COMMAND_CODE_API_KEY")
            or env.get("COMMANDCODE_API_KEY")
            or env.get("COHERE_API_KEY")
            or env.get("OPENROUTER_API_KEY")
            or env.get("OPENAI_API_KEY")
            or env.get("GEMINI_API_KEY")
            or env.get("ANTHROPIC_API_KEY")
        )
        provider_label = "Command Code (Taste-1 / Cohere)"
        key_env_var = "COMMAND_CODE_API_KEY / COHERE_API_KEY / GEMINI_API_KEY"
        if env.get("GEMINI_API_KEY") and not (
            env.get("COMMAND_CODE_API_KEY")
            or env.get("COMMANDCODE_API_KEY")
            or env.get("COHERE_API_KEY")
            or env.get("OPENROUTER_API_KEY")
            or env.get("OPENAI_API_KEY")
        ):
            is_gemini = True
        else:
            is_openai = True
    elif is_opencode:
        # OpenCode supports OpenCode API, OpenRouter, DeepSeek, OpenAI, Gemini, or Anthropic
        api_key = (
            env.get("OPENCODE_API_KEY")
            or env.get("OPENCODE_KEY")
            or env.get("OPENROUTER_API_KEY")
            or env.get("DEEPSEEK_API_KEY")
            or env.get("OPENAI_API_KEY")
            or env.get("GEMINI_API_KEY")
            or env.get("ANTHROPIC_API_KEY")
        )
        provider_label = "OpenCode Provider"
        key_env_var = "OPENCODE_API_KEY / OPENROUTER_API_KEY / GEMINI_API_KEY"
        if env.get("GEMINI_API_KEY") and not (env.get("OPENCODE_API_KEY") or env.get("OPENROUTER_API_KEY") or env.get("DEEPSEEK_API_KEY") or env.get("OPENAI_API_KEY")):
            is_gemini = True
        else:
            is_openai = True
    elif is_gemini:
        api_key = env.get("GEMINI_API_KEY") or env.get("GOOGLE_API_KEY") or _read_cli_auth("gemini")
        provider_label = "Google Antigravity (Gemini)"
        key_env_var = "GEMINI_API_KEY"
    elif is_claude:
        api_key = env.get("ANTHROPIC_API_KEY") or env.get("CLAUDE_API_KEY") or _read_cli_auth("claude")
        provider_label = "Anthropic Claude"
        key_env_var = "ANTHROPIC_API_KEY"
    elif is_openai:
        api_key = env.get("OPENAI_API_KEY") or env.get("CODEX_API_KEY") or _read_cli_auth("openai")
        provider_label = "OpenAI Codex"
        key_env_var = "OPENAI_API_KEY"
    else:
        # Fallback to Gemini if key exists, else OpenAI / Claude
        api_key = (
            env.get("GEMINI_API_KEY")
            or env.get("ANTHROPIC_API_KEY")
            or env.get("OPENAI_API_KEY")
            or _read_cli_auth("gemini")
            or _read_cli_auth("claude")
            or _read_cli_auth("openai")
        )
        if env.get("GEMINI_API_KEY") or _read_cli_auth("gemini"):
            is_gemini = True
        elif env.get("ANTHROPIC_API_KEY") or _read_cli_auth("claude"):
            is_claude = True
        elif env.get("OPENAI_API_KEY") or _read_cli_auth("openai"):
            is_openai = True

    if not api_key:
        yield {
            "type": "error",
            "message": (
                f"No API key configured for {provider_label}. "
                f"Please add `{key_env_var}` in Settings > Secrets Vault to start chatting."
            ),
        }
        return

    # Yield initial connection status
    resolved_model_name = (
        _map_gemini_model_name(model)
        if is_gemini
        else (_map_claude_model_name(model) if is_claude else _map_openai_model_name(model))
    )

    yield {
        "type": "status",
        "harness": harness,
        "model": resolved_model_name,
        "cwd": working_dir,
    }

    provider_family = "gemini" if is_gemini else ("claude" if is_claude else "openai")
    session_id = f"{chat_id or 'chat'}_{provider_family}"
    if session_id not in _chat_histories:
        _chat_histories[session_id] = []

    history = _chat_histories[session_id]

    # System instruction guiding the workspace agent
    system_instruction = (
        f"You are an expert autonomous AI software engineer working inside a sandboxed repository worktree at '{working_dir}'.\n"
        "You have full direct workspace tools to inspect files, edit/write code, list directories, run shell commands/tests, "
        "and inspect Git status/diffs.\n\n"
        "CRITICAL OPERATIONAL RULES:\n"
        "1. PROACTIVE ACTION: When the user asks you to modify code, change styles/backgrounds, add features, or fix bugs, "
        "DO NOT just output code snippets or descriptions passively. You MUST immediately use your workspace tools "
        "(`read_file`, `edit_file`, `write_file`, `run_command`) to inspect and modify the actual files in the repository worktree.\n"
        "2. SAFE EDITING: Always use `read_file` to verify existing file contents and exact lines before using `edit_file` or `write_file`.\n"
        "3. CONCISE REASONING: Keep conversational commentary concise, confirming the changes made to the files.\n"
    )

    if is_gemini:
        async for evt in _run_gemini_loop(
            api_key=api_key,
            model_name=resolved_model_name,
            prompt=prompt,
            working_dir=working_dir,
            history=history,
            system_instruction=system_instruction,
        ):
            yield evt
    elif is_claude:
        async for evt in _run_claude_loop(
            api_key=api_key,
            model_name=resolved_model_name,
            prompt=prompt,
            working_dir=working_dir,
            history=history,
            system_instruction=system_instruction,
        ):
            yield evt
    elif is_openai:
        async for evt in _run_openai_loop(
            api_key=api_key,
            model_name=resolved_model_name,
            prompt=prompt,
            working_dir=working_dir,
            history=history,
            system_instruction=system_instruction,
        ):
            yield evt


async def _run_gemini_loop(
    api_key: str,
    model_name: str,
    prompt: str,
    working_dir: str,
    history: list[dict[str, Any]],
    system_instruction: str,
) -> AsyncGenerator[dict[str, Any], None]:
    """Execute Gemini agent loop with native function calling."""
    # Convert tools to Gemini function declarations
    gemini_tools = [
        {
            "functionDeclarations": [
                {
                    "name": t["name"],
                    "description": t["description"],
                    "parameters": t.get("parameters", {}),
                }
                for t in WORKSPACE_TOOLS_SCHEMA
            ]
        }
    ]

    # Append user prompt to session contents
    history.append({
        "role": "user",
        "parts": [{"text": prompt}],
    })

    current_model = model_name
    fallback_pool = [m for m in ["gemini-2.5-flash", "gemini-2.0-flash", "gemini-1.5-flash", "gemini-2.5-pro", "gemini-1.5-pro"] if m != current_model]

    headers = {"Content-Type": "application/json"}
    max_turns = 10
    turn_count = 0
    full_response_text = []

    async with httpx.AsyncClient(timeout=120.0) as client:
        while turn_count < max_turns:
            turn_count += 1
            payload = {
                "contents": history,
                "systemInstruction": {"parts": [{"text": system_instruction}]},
                "tools": gemini_tools,
                "generationConfig": {
                    "temperature": 0.4,
                    "maxOutputTokens": 8192,
                },
            }

            resp = None
            while True:
                url = f"https://generativelanguage.googleapis.com/v1beta/models/{current_model}:generateContent?key={api_key}"
                try:
                    resp = await client.post(url, headers=headers, json=payload)
                except Exception as e:
                    yield {"type": "error", "message": f"Network error contacting Gemini API: {e}"}
                    return

                if resp.status_code in (404, 429) and fallback_pool:
                    current_model = fallback_pool.pop(0)
                    continue
                break

            if resp.status_code != 200:
                err_text = resp.text
                try:
                    err_json = resp.json()
                    err_text = err_json.get("error", {}).get("message", resp.text)
                except Exception:
                    pass
                yield {"type": "error", "message": f"Gemini API Error ({resp.status_code}): {err_text}"}
                return

            data = resp.json()
            candidates = data.get("candidates", [])
            if not candidates:
                break

            candidate = candidates[0]
            content = candidate.get("content", {})
            parts = content.get("parts", [])

            # Save model turn to history
            history.append(content)

            function_calls = []
            for part in parts:
                if "text" in part and part["text"]:
                    text_chunk = part["text"]
                    full_response_text.append(text_chunk)
                    yield {"type": "chunk", "text": text_chunk}
                if "functionCall" in part:
                    function_calls.append(part["functionCall"])

            if not function_calls:
                # No more tools called; completed successfully
                break

            # Execute all function calls
            response_parts = []
            for fc in function_calls:
                call_name = fc.get("name", "")
                call_args = fc.get("args", {})

                yield {
                    "type": "tool_call",
                    "name": call_name,
                    "args": call_args,
                }

                tool_result_str = await dispatch_tool_call(working_dir, call_name, call_args)

                yield {
                    "type": "tool_result",
                    "name": call_name,
                    "result": tool_result_str,
                }

                response_parts.append({
                    "functionResponse": {
                        "name": call_name,
                        "response": {"output": tool_result_str},
                    }
                })

            # Append tool responses back into the history for the model to see
            history.append({
                "role": "user",
                "parts": response_parts,
            })

    final_text = "".join(full_response_text)
    yield {"type": "done", "text": final_text}


async def _run_claude_loop(
    api_key: str,
    model_name: str,
    prompt: str,
    working_dir: str,
    history: list[dict[str, Any]],
    system_instruction: str,
) -> AsyncGenerator[dict[str, Any], None]:
    """Execute Anthropic Claude agent loop with tool use."""
    claude_tools = [
        {
            "name": t["name"],
            "description": t["description"],
            "input_schema": t.get("parameters", {"type": "object", "properties": {}}),
        }
        for t in WORKSPACE_TOOLS_SCHEMA
    ]

    history.append({"role": "user", "content": prompt})

    url = "https://api.anthropic.com/v1/messages"
    headers = {
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }

    max_turns = 10
    turn_count = 0
    full_response_text = []

    async with httpx.AsyncClient(timeout=120.0) as client:
        while turn_count < max_turns:
            turn_count += 1
            payload = {
                "model": model_name,
                "system": system_instruction,
                "messages": history,
                "tools": claude_tools,
                "max_tokens": 4096,
            }

            try:
                resp = await client.post(url, headers=headers, json=payload)
            except Exception as e:
                yield {"type": "error", "message": f"Network error contacting Anthropic API: {e}"}
                return

            if resp.status_code != 200:
                yield {"type": "error", "message": f"Anthropic API Error ({resp.status_code}): {resp.text}"}
                return

            data = resp.json()
            content_blocks = data.get("content", [])
            history.append({"role": "assistant", "content": content_blocks})

            tool_calls = []
            for block in content_blocks:
                if block.get("type") == "text":
                    text_chunk = block.get("text", "")
                    full_response_text.append(text_chunk)
                    yield {"type": "chunk", "text": text_chunk}
                elif block.get("type") == "tool_use":
                    tool_calls.append(block)

            if not tool_calls or data.get("stop_reason") != "tool_use":
                break

            tool_results = []
            for t_use in tool_calls:
                call_id = t_use.get("id")
                call_name = t_use.get("name", "")
                call_args = t_use.get("input", {})

                yield {"type": "tool_call", "name": call_name, "args": call_args}
                res_text = await dispatch_tool_call(working_dir, call_name, call_args)
                yield {"type": "tool_result", "name": call_name, "result": res_text}

                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": call_id,
                    "content": res_text,
                })

            history.append({"role": "user", "content": tool_results})

    final_text = "".join(full_response_text)
    yield {"type": "done", "text": final_text}


def _sanitize_openai_messages(messages: list[dict[str, Any]], system_instruction: str) -> list[dict[str, Any]]:
    """Clean and repair message history for strict OpenAI / OpenRouter / Azure compliance.

    Guarantees:
    1. System message with system_instruction is first.
    2. Incompatible foreign message formats are converted or filtered.
    3. Every assistant message with 'tool_calls' is immediately followed by 'tool' messages
       for every single tool_call_id. Missing tool responses are synthesized so OpenAI / Azure /
       OpenRouter never reject the request with a 400 error.
    """
    sanitized: list[dict[str, Any]] = []

    # Check if a system message is already first
    has_system = any(isinstance(m, dict) and m.get("role") == "system" for m in messages)
    if not has_system and system_instruction:
        sanitized.append({"role": "system", "content": system_instruction})

    i = 0
    while i < len(messages):
        msg = messages[i]
        if not isinstance(msg, dict):
            i += 1
            continue

        role = msg.get("role")
        if role not in ("system", "user", "assistant", "tool"):
            # If from Gemini (parts) or Claude format, convert to simple user/assistant text
            if "parts" in msg:
                text_parts = [p.get("text", "") for p in msg.get("parts", []) if isinstance(p, dict) and "text" in p]
                conv_role = "user" if role == "user" else "assistant"
                sanitized.append({"role": conv_role, "content": " ".join(text_parts) or "..."})
            i += 1
            continue

        if role == "assistant" and msg.get("tool_calls"):
            tool_calls = msg.get("tool_calls", [])
            expected_ids = [tc.get("id") for tc in tool_calls if isinstance(tc, dict) and tc.get("id")]
            sanitized.append(msg)
            i += 1

            # Gather consecutive tool response messages
            found_ids = set()
            while i < len(messages) and isinstance(messages[i], dict) and messages[i].get("role") == "tool":
                t_msg = messages[i]
                t_id = t_msg.get("tool_call_id")
                if t_id:
                    found_ids.add(t_id)
                sanitized.append(t_msg)
                i += 1

            # Synthesize responses for any missing tool_call_ids so OpenAI/OpenRouter is satisfied
            for tid in expected_ids:
                if tid not in found_ids:
                    sanitized.append({
                        "role": "tool",
                        "tool_call_id": tid,
                        "content": "Tool execution completed.",
                    })
            continue

        sanitized.append(msg)
        i += 1

    return sanitized


async def _run_openai_loop(
    api_key: str,
    model_name: str,
    prompt: str,
    working_dir: str,
    history: list[dict[str, Any]],
    system_instruction: str,
) -> AsyncGenerator[dict[str, Any], None]:
    """Execute OpenAI agent loop with function calling."""
    openai_tools = [
        {
            "type": "function",
            "function": {
                "name": t["name"],
                "description": t["description"],
                "parameters": t.get("parameters", {}),
            },
        }
        for t in WORKSPACE_TOOLS_SCHEMA
    ]

    history.append({"role": "user", "content": prompt})

    is_opencode_gateway = api_key.startswith("oc_sk_")
    is_commandcode_api = api_key.startswith("user_")
    is_openrouter = api_key.startswith(("sk-or-", "sk_or_")) or (
        ("opencode" in model_name.lower() or "/" in model_name or ":free" in model_name)
        and not (is_opencode_gateway or is_commandcode_api)
    )

    if is_commandcode_api:
        url = "https://api.commandcode.ai/provider/v1/chat/completions"
    elif is_opencode_gateway:
        url = "https://gateway.opencode.ai/v1/chat/completions"
    elif is_openrouter:
        url = "https://openrouter.ai/api/v1/chat/completions"
    else:
        url = "https://api.openai.com/v1/chat/completions"

    if is_commandcode_api:
        m_lower = model_name.lower()
        if "ling-3.0" in m_lower or "sante" in m_lower:
            model_name = "inclusionai/ling-3.0-flash-sante:free"
        elif "ling-3.1" in m_lower or "ling" in m_lower:
            model_name = "inclusionai/ling-3.1-flash:free"
        elif "mimo" in m_lower:
            model_name = "xiaomi/mimo-v2.5-pro"
        elif "glyph" in m_lower:
            model_name = "stealth/glyph-cluster:free"
        elif "laguna" in m_lower:
            model_name = "poolside/laguna-s-2.1-free"
        elif "gemini-3.8" in m_lower:
            model_name = "google/gemini-3.8-flash"
        elif "gemini" in m_lower:
            model_name = "google/gemini-3.7-flash"
        elif "qwen" in m_lower:
            model_name = "Qwen/Qwen3.8-Omni-Flash"
        elif "grok" in m_lower:
            model_name = "xai/grok-4.7"
        elif "/" in model_name:
            pass  # keep raw model id
        else:
            model_name = "inclusionai/ling-3.0-flash-sante:free"
    elif is_openrouter:
        m_lower = model_name.lower().strip()
        if "/" in model_name and not (" " in model_name):
            # Already a valid explicit provider/model identifier (e.g. 'openai/gpt-4o', 'deepseek/deepseek-chat')
            pass
        elif "luna-pro" in m_lower:
            model_name = "openai/gpt-5.6-luna-pro"
        elif "luna" in m_lower or "5.6" in m_lower:
            model_name = "openai/gpt-5.6-luna"
        elif "codestral" in m_lower or "mistral" in m_lower:
            model_name = "mistralai/codestral-2508"
        elif "qwen" in m_lower:
            model_name = "qwen/qwen3-coder"
        elif "deepseek-r1" in m_lower or " r1" in m_lower:
            model_name = "deepseek/deepseek-r1"
        elif "deepseek" in m_lower or "v3" in m_lower:
            model_name = "deepseek/deepseek-chat"
        elif "claude" in m_lower or "sonnet" in m_lower:
            model_name = "anthropic/claude-sonnet-4.5"
        elif "4o-mini" in m_lower or "mini" in m_lower:
            model_name = "openai/gpt-4o-mini"
        elif "gpt" in m_lower or "o3" in m_lower or "4o" in m_lower:
            model_name = "openai/gpt-4o"
        elif "llama" in m_lower:
            model_name = "meta-llama/llama-3.3-70b-instruct"
        elif "ling" in m_lower:
            model_name = "inclusionai/ling-3.0-flash-sante:free"
        elif "taste" in m_lower:
            model_name = "commandcode/taste-1"
        else:
            model_name = "openai/gpt-4o"

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    if is_openrouter:
        headers["HTTP-Referer"] = "https://congruence.dev"
        headers["X-Title"] = "Congruence"

    max_turns = 10
    turn_count = 0
    full_response_text = []

    async with httpx.AsyncClient(timeout=120.0) as client:
        while turn_count < max_turns:
            turn_count += 1
            sanitized_messages = _sanitize_openai_messages(history, system_instruction)
            payload = {
                "model": model_name,
                "messages": sanitized_messages,
                "tools": openai_tools,
                "tool_choice": "auto",
            }

            try:
                resp = await client.post(url, headers=headers, json=payload)
            except Exception as e:
                yield {"type": "error", "message": f"Network error contacting API: {e}"}
                return

            if resp.status_code != 200:
                if is_commandcode_api:
                    yield {
                        "type": "error",
                        "message": (
                            f"Command Code API Error ({resp.status_code}): {resp.text}\n\n"
                            "💡 Tip: Verify your credit balance on https://commandcode.ai Studio, or use Google Antigravity (Gemini) in the model picker."
                        ),
                    }
                    return
                if is_openrouter:
                    yield {
                        "type": "error",
                        "message": (
                            f"OpenRouter API Error ({resp.status_code}): {resp.text}\n\n"
                            "💡 Tip: Verify your credit balance and API key on https://openrouter.ai/keys, or switch to Google Antigravity (Gemini) in the model picker."
                        ),
                    }
                    return
                if is_opencode_gateway and resp.status_code == 404:
                    yield {
                        "type": "error",
                        "message": (
                            f"OpenCode Gateway Error (404): {resp.text}\n\n"
                            "💡 Tip: On https://app.opencode.ai, ensure your team has a provider (like OpenAI, OpenRouter, or Anthropic) linked or model credits enabled under Team Settings."
                        ),
                    }
                    return
                yield {"type": "error", "message": f"API Error ({resp.status_code}): {resp.text}"}
                return

            data = resp.json()
            choices = data.get("choices", [])
            if not choices:
                break

            msg = choices[0].get("message", {})
            history.append(msg)

            content = msg.get("content")
            if content:
                full_response_text.append(content)
                yield {"type": "chunk", "text": content}

            tool_calls = msg.get("tool_calls", [])
            if not tool_calls and content:
                # Text fallback: extract any tool invocations embedded in raw text or token-leaked templates
                fallback_calls = _extract_text_tool_calls(content)
                if fallback_calls:
                    for fc in fallback_calls:
                        call_name = fc["name"]
                        call_args = fc["args"]
                        yield {"type": "tool_call", "name": call_name, "args": call_args}
                        try:
                            res_text = await dispatch_tool_call(working_dir, call_name, call_args)
                        except Exception as ex:
                            res_text = f"Tool execution error: {ex}"
                        yield {"type": "tool_result", "name": call_name, "result": res_text}
                        history.append({
                            "role": "user",
                            "content": f"[Tool Result for {call_name}]:\n{res_text}",
                        })
                    continue

            if not tool_calls:
                break

            for tc in tool_calls:
                call_id = tc.get("id")
                fn = tc.get("function", {})
                call_name = fn.get("name", "")
                try:
                    call_args = json.loads(fn.get("arguments", "{}"))
                except Exception:
                    call_args = {}

                yield {"type": "tool_call", "name": call_name, "args": call_args}
                try:
                    res_text = await dispatch_tool_call(working_dir, call_name, call_args)
                except Exception as ex:
                    res_text = f"Tool execution error: {ex}"
                yield {"type": "tool_result", "name": call_name, "result": res_text}

                history.append({
                    "role": "tool",
                    "tool_call_id": call_id,
                    "content": str(res_text),
                })

    final_text = "".join(full_response_text)
    yield {"type": "done", "text": final_text}


def _extract_text_tool_calls(content: str) -> list[dict[str, Any]]:
    """Extract tool calls from models that output tool calls in raw text or leaked templates."""
    import re
    if not content:
        return []
    calls: list[dict[str, Any]] = []
    
    # Pattern 1: read_file json { ... } or edit_file { ... }
    pattern1 = re.compile(
        r'(read_file|edit_file|write_file|list_dir|run_command)\s*(?:json)?\s*(\{[\s\S]*?\})',
        re.IGNORECASE,
    )
    for match in pattern1.finditer(content):
        fn_name = match.group(1).lower()
        args_str = match.group(2)
        try:
            args = json.loads(args_str)
            if isinstance(args, dict):
                calls.append({"name": fn_name, "args": args})
        except Exception:
            pass

    # Pattern 2: {"name": "read_file", "arguments": {...}}
    if not calls:
        pattern2 = re.compile(
            r'\{[^{}]*"name"\s*:\s*"(read_file|edit_file|write_file|list_dir|run_command)"[^{}]*\}',
            re.IGNORECASE,
        )
        for match in pattern2.finditer(content):
            try:
                data = json.loads(match.group(0))
                fn_name = data.get("name")
                args = data.get("arguments") or data.get("parameters") or {}
                if isinstance(args, str):
                    args = json.loads(args)
                if fn_name and isinstance(args, dict):
                    calls.append({"name": fn_name, "args": args})
            except Exception:
                pass

    return calls

