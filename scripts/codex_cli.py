#!/usr/bin/env python3
"""OpenAI Codex CLI (codex) - Container Terminal Agent & Runner.

Provides interactive and programmatic OpenAI Codex / GPT-4o execution inside
Congruence sandboxed worktrees and Docker containers with authentic API tool calling.
"""

import os
import sys
import json
import time
import subprocess
import urllib.request
import urllib.error
import readline
from pathlib import Path
from typing import Any, Dict, List, Tuple

VERSION = "2.0.0-congruence"

CYAN = "\x1b[1;36m"
GREEN = "\x1b[1;32m"
YELLOW = "\x1b[1;33m"
BLUE = "\x1b[1;34m"
RED = "\x1b[1;31m"
MUTED = "\x1b[0;90m"
BOLD = "\x1b[1m"
RESET = "\x1b[0m"

BANNER = f"""{CYAN}   ____                   ___    ____
  / __ \\____  ___  ____  /   |  /  _/
 / / / / __ \\/ _ \\/ __ \\/ /| |  / /  
/ /_/ / /_/ /  __/ / / / ___ |_/ /   
\\____/ .___/\\___/_/ /_/_/  |_/___/   
    /_/     {MUTED}v{VERSION} [Live OpenAI Agent]{RESET}
"""

DEFAULT_MODEL = "gpt-4o"

SYSTEM_PROMPT = """You are OpenAI Codex, an autonomous AI pair programmer operating directly in the repository workspace.
You can read files, write code edits, list directories, and execute shell commands to build and verify solutions.
"""

def get_config_dir() -> Path:
    d = Path.home() / ".codex"
    d.mkdir(parents=True, exist_ok=True)
    return d

def get_auth_file() -> Path:
    return get_config_dir() / "auth.json"

def load_auth() -> Dict[str, Any]:
    f = get_auth_file()
    if f.exists():
        try:
            return json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}

def save_auth(data: Dict[str, Any]) -> None:
    f = get_auth_file()
    f.write_text(json.dumps(data, indent=2), encoding="utf-8")

def resolve_credentials() -> Tuple[str, str, str]:
    auth = load_auth()
    if os.environ.get("OPENAI_API_KEY"):
        return "openai", os.environ["OPENAI_API_KEY"].strip(), auth.get("model", DEFAULT_MODEL)
    if os.environ.get("OPENROUTER_API_KEY"):
        return "openrouter", os.environ["OPENROUTER_API_KEY"].strip(), auth.get("model", "openai/gpt-4o")
    if auth.get("token") and not auth.get("token", "").startswith("sk-codex-sess-"):
        return auth.get("provider", "openai"), auth["token"].strip(), auth.get("model", DEFAULT_MODEL)
    if auth.get("api_key") and not auth.get("api_key", "").startswith("sk-codex-sess-"):
        return auth.get("provider", "openai"), auth["api_key"].strip(), auth.get("model", DEFAULT_MODEL)
    return "none", "", auth.get("model", DEFAULT_MODEL)

# ----------------- Tools -----------------

def tool_run_command(command: str) -> str:
    try:
        res = subprocess.run(command, shell=True, capture_output=True, text=True, timeout=120, cwd=os.getcwd())
        out = (res.stdout or "") + (f"\n[stderr]\n{res.stderr}" if res.stderr else "")
        if res.returncode != 0:
            out += f"\n[exit code: {res.returncode}]"
        return out.strip() or "[Command finished with no output]"
    except Exception as e:
        return f"[Error: {e}]"

def tool_read_file(path: str) -> str:
    try:
        target = (Path(os.getcwd()) / path).resolve()
        if not target.exists():
            return f"[Error: File '{path}' not found]"
        return target.read_text(encoding="utf-8", errors="replace")[:10000]
    except Exception as e:
        return f"[Error reading file: {e}]"

def tool_write_file(path: str, content: str) -> str:
    try:
        target = (Path(os.getcwd()) / path).resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return f"Successfully wrote {len(content)} bytes to {path}"
    except Exception as e:
        return f"[Error writing file: {e}]"

def tool_list_files(path: str = ".") -> str:
    try:
        target = (Path(os.getcwd()) / path).resolve()
        items = [p.name + ("/" if p.is_dir() else "") for p in sorted(target.iterdir()) if not p.name.startswith(".git")]
        return "\n".join(items) or "[Empty directory]"
    except Exception as e:
        return f"[Error: {e}]"

OPENAI_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "run_command",
            "description": "Run shell command in workspace",
            "parameters": {
                "type": "object",
                "properties": {"command": {"type": "string"}},
                "required": ["command"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read file text",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Write or modify file contents",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"}
                },
                "required": ["path", "content"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "list_files",
            "description": "List files in directory",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string"}},
            }
        }
    }
]

def execute_tool(name: str, args: Dict[str, Any]) -> str:
    if name == "run_command":
        cmd = args.get("command", "")
        print(f"  {YELLOW}⚡ [exec]{RESET} {cmd}")
        return tool_run_command(cmd)
    elif name == "read_file":
        path = args.get("path", "")
        print(f"  {BLUE}📖 [read]{RESET} {path}")
        return tool_read_file(path)
    elif name == "write_file":
        path = args.get("path", "")
        content = args.get("content", "")
        print(f"  {GREEN}✍️  [write]{RESET} {path} ({len(content)} bytes)")
        return tool_write_file(path, content)
    elif name == "list_files":
        path = args.get("path", ".")
        print(f"  {CYAN}📂 [list]{RESET} {path}")
        return tool_list_files(path)
    return f"Unknown tool: {name}"

def call_openai_chat(messages: List[Dict[str, Any]], api_key: str, model: str, provider: str = "openai") -> Dict[str, Any]:
    url = "https://api.openai.com/v1/chat/completions" if provider == "openai" else "https://openrouter.ai/api/v1/chat/completions"
    payload = {
        "model": model,
        "messages": messages,
        "tools": OPENAI_TOOLS
    }
    data_bytes = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data_bytes,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}"
        },
        method="POST"
    )
    with urllib.request.urlopen(req, timeout=90) as resp:
        return json.loads(resp.read().decode("utf-8"))

def run_agent_turn(prompt: str) -> None:
    provider, api_key, model = resolve_credentials()
    if provider == "none" or not api_key:
        print(f"\n{YELLOW}⚠️  No OpenAI API Key found.{RESET}")
        print(f"  1. Run `{BOLD}codex login{RESET}` to authenticate")
        print(f"  2. Or export {BOLD}OPENAI_API_KEY=\"sk-...\"{RESET}\n")
        return

    print(f"\n{MUTED}⠋ [codex] Thinking with {model} in {os.getcwd()}...{RESET}")
    messages: List[Dict[str, Any]] = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": prompt}
    ]

    for _ in range(10):
        try:
            resp = call_openai_chat(messages, api_key, model, provider)
        except Exception as e:
            print(f"\n{RED}Error calling OpenAI API:{RESET} {e}\n")
            return

        choice = resp.get("choices", [{}])[0]
        msg = choice.get("message", {})
        messages.append(msg)

        tool_calls = msg.get("tool_calls", [])
        if not tool_calls:
            print(f"\n{CYAN}OpenAI Codex:{RESET}")
            print(msg.get("content", "").strip())
            print()
            return

        # Execute tool calls
        for tc in tool_calls:
            call_id = tc.get("id")
            func = tc.get("function", {})
            name = func.get("name")
            try:
                args = json.loads(func.get("arguments", "{}"))
            except Exception:
                args = {}
            res_str = execute_tool(name, args)
            messages.append({
                "role": "tool",
                "tool_call_id": call_id,
                "content": res_str
            })

def handle_login() -> None:
    print(f"\n{CYAN}◆ OpenAI Codex Authentication Gateway{RESET}")
    print(f"{MUTED}Linking container worktree ({os.getcwd()}) to OpenAI API...{RESET}\n")
    print(f"{YELLOW}Enter your OpenAI API key (from https://platform.openai.com/api-keys):{RESET}")
    try:
        token_input = input(f"{MUTED}OpenAI API Key [sk-...]: {RESET}").strip()
    except (KeyboardInterrupt, EOFError):
        return

    if not token_input:
        print("Login cancelled.")
        return

    auth_data = {
        "authenticated": True,
        "provider": "openai",
        "model": DEFAULT_MODEL,
        "token": token_input,
        "api_key": token_input,
        "worktree": os.getcwd(),
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    save_auth(auth_data)
    
    zshrc = Path.home() / ".zshrc"
    try:
        with open(zshrc, "a", encoding="utf-8") as f:
            f.write(f'\nexport OPENAI_API_KEY="{token_input}"\n')
    except Exception:
        pass
        
    print(f"\n{GREEN}✓ OpenAI Codex authenticated successfully!{RESET}")
    print(f"  • Credentials stored at: {MUTED}{get_auth_file()}{RESET}")
    print(f"  • Active Model: {GREEN}{DEFAULT_MODEL}{RESET}\n")

def check_status() -> None:
    provider, api_key, model = resolve_credentials()
    if provider != "none" and api_key:
        masked = api_key[:6] + "..." + api_key[-4:] if len(api_key) > 10 else "***"
        print(f"\n{GREEN}● Connected to OpenAI Codex{RESET}")
        print(f"  Provider: {provider}")
        print(f"  Model:    {model}")
        print(f"  Key:      {masked}\n")
    else:
        print(f"\n{YELLOW}○ Disconnected{RESET} - Run `{BOLD}codex login{RESET}` to authenticate.\n")

def interactive_session() -> None:
    print(BANNER)
    provider, api_key, model = resolve_credentials()
    status_str = f"{GREEN}Connected{RESET}" if provider != "none" and api_key else f"{YELLOW}Not logged in{RESET}"
    print(f"{MUTED}Status: {status_str} | Model: {GREEN}{model}{MUTED} | Worktree: {BLUE}{os.getcwd()}{RESET}")
    print(f"{MUTED}Type your instruction or 'exit' to quit.{RESET}\n")
    
    while True:
        try:
            line = input(f"{CYAN}codex ➜ {RESET}").strip()
            if not line:
                continue
            if line in ("exit", "quit", ":q"):
                print(f"{MUTED}Exiting Codex session.{RESET}")
                break
            run_agent_turn(line)
        except (KeyboardInterrupt, EOFError):
            print(f"\n{MUTED}Session ended.{RESET}")
            break

def main() -> None:
    args = sys.argv[1:]
    if not args:
        interactive_session()
        return

    cmd = args[0]
    if cmd in ("--help", "-h", "help"):
        print("Usage: codex [OPTIONS] [COMMAND]")
        print("\nCommands:")
        print("  login           Authenticate OpenAI Codex with API key")
        print("  status          Check connection status")
        print("\nOptions:")
        print("  -p, --prompt <text>   Run single prompt")
        print("  -v, --version         Show version")
        return

    if cmd in ("--version", "-v", "version"):
        print(f"codex version {VERSION}")
        return

    if cmd in ("login", "auth"):
        handle_login()
        return

    if cmd in ("status", "whoami"):
        check_status()
        return

    if cmd in ("--prompt", "-p"):
        prompt = " ".join(args[1:]) if len(args) > 1 else ""
        if not prompt:
            print("Error: --prompt requires text argument.")
            sys.exit(1)
        run_agent_turn(prompt)
        return

    run_agent_turn(" ".join(args))

if __name__ == "__main__":
    main()
