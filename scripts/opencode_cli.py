#!/usr/bin/env python3
"""OpenCode CLI runner for Congruence sandboxed containers."""

import os
import sys
import json
import time
import subprocess
import urllib.request
import urllib.error
from pathlib import Path
from typing import Any, Dict, List

VERSION = "1.6.0-congruence"

CYAN = "\x1b[1;36m"
GREEN = "\x1b[1;32m"
YELLOW = "\x1b[1;33m"
MUTED = "\x1b[0;90m"
BOLD = "\x1b[1m"
RESET = "\x1b[0m"

def get_auth_file() -> Path:
    d = Path.home() / ".local" / "share" / "opencode"
    d.mkdir(parents=True, exist_ok=True)
    return d / "auth.json"

def get_api_key() -> str:
    if os.environ.get("OPENROUTER_API_KEY"):
        return os.environ["OPENROUTER_API_KEY"].strip()
    if os.environ.get("OPENAI_API_KEY"):
        return os.environ["OPENAI_API_KEY"].strip()
    f = get_auth_file()
    if f.exists():
        try:
            d = json.loads(f.read_text())
            return d.get("token", "") or d.get("api_key", "")
        except Exception:
            pass
    return ""

def main():
    args = sys.argv[1:]
    auth_file = get_auth_file()
    
    if not args or args[0] in ("auth", "login"):
        print(f"\n{CYAN}◆ OpenCode Authentication Gateway{RESET}")
        print("Authenticating with OpenRouter / OpenCode provider registry...")
        try:
            key_input = input(f"{MUTED}Enter OpenRouter or OpenCode API Key: {RESET}").strip()
        except (KeyboardInterrupt, EOFError):
            return
            
        if not key_input:
            print("Login cancelled.")
            return

        auth_data = {
            "authenticated": True,
            "provider": "openrouter",
            "token": key_input,
            "api_key": key_input,
            "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        }
        auth_file.write_text(json.dumps(auth_data, indent=2))
        print(f"\n{GREEN}✓ OpenCode authenticated successfully in container.{RESET}")
        print(f"  Configuration saved to {auth_file}\n")
        return

    if args[0] in ("status", "whoami"):
        key = get_api_key()
        if key:
            print(f"\n{GREEN}● Connected to OpenCode Gateway{RESET}\n")
        else:
            print(f"\n{YELLOW}○ Disconnected{RESET} - Run `opencode auth login` to authenticate.\n")
        return

    prompt = " ".join(args)
    key = get_api_key()
    if not key:
        print(f"{YELLOW}⚠️  No API Key found. Run `opencode auth login` or set OPENROUTER_API_KEY.{RESET}")
        return

    print(f"{MUTED}⠋ [opencode] Executing with OpenRouter in {os.getcwd()}...{RESET}")
    url = "https://openrouter.ai/api/v1/chat/completions"
    payload = {
        "model": "anthropic/claude-3.5-sonnet",
        "messages": [
            {"role": "system", "content": "You are OpenCode, an AI coding assistant. Help the user in their repository."},
            {"role": "user", "content": prompt}
        ]
    }
    data_bytes = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data_bytes,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"},
        method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=90) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            out = data.get("choices", [{}])[0].get("message", {}).get("content", "")
            print(f"\n{CYAN}OpenCode:{RESET}\n{out.strip()}\n")
    except Exception as e:
        print(f"Error calling OpenRouter: {e}")

if __name__ == "__main__":
    main()
