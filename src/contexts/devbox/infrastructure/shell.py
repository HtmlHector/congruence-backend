"""PTY bridge for interactive shells inside local devbox containers.

Browsers cannot attach a TTY, so the server allocates a pseudo-terminal, runs
``docker exec -it`` on its slave side, and proxies master-side bytes over a
WebSocket: binary frames are raw terminal I/O, text frames are JSON control
messages (currently ``{"type": "resize", "cols": n, "rows": n}``).

Each browser terminal tab identifies itself with a stable session name. The
shell runs inside a tmux session of that name, so reloading the page (or
reopening a tab) reattaches to the same process instead of starting a new one:
running agents such as Claude Code or Codex keep their conversation and screen.
"""

from __future__ import annotations

import asyncio
import fcntl
import json
import os
import pty
import re
import shlex
import signal
import struct
import termios
from typing import Any

import structlog
from fastapi import WebSocket

from src.contexts.devbox.infrastructure.providers.local import docker_executable

logger = structlog.get_logger(__name__)

READ_SIZE = 65536
DEFAULT_COLS = 120
DEFAULT_ROWS = 32

# Wipes a restored client-side preview when no session survived to repaint it.
CLEAR_SCREEN = b"\x1b[H\x1b[2J\x1b[3J"

SESSION_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
TMUX_HISTORY_LIMIT = 50000


def session_name(session: str) -> str:
    """Return the tmux session name for a terminal tab's session id."""
    return f"cng-{session}"


def build_attach_script(cwd: str, session: str) -> str:
    """Return the in-container script that attaches to (or starts) a tmux session.

    ``new-session -A`` attaches when the session already exists and creates it
    otherwise, which is what makes a reload resume the same shell. Images without
    tmux fall back to a plain login shell so the terminal still works.
    """
    name = shlex.quote(session_name(session))
    workdir = shlex.quote(cwd)
    return (
        "if command -v tmux >/dev/null 2>&1; then "
        "tmux start-server 2>/dev/null; "
        # The workspace already draws chrome around the terminal, so tmux's own
        # status bar would just be a second, redundant strip.
        "tmux set-option -g status off 2>/dev/null; "
        f"tmux set-option -g history-limit {TMUX_HISTORY_LIMIT} 2>/dev/null; "
        # A reload leaves the previous client attached to the session: docker keeps
        # its TTY open after the browser goes away, so it would linger forever.
        # A tab owns exactly one client, so the one attaching now evicts the rest.
        f"if tmux has-session -t {name} 2>/dev/null; then "
        f"for tty in $(tmux list-clients -t {name} -F '#{{client_tty}}' 2>/dev/null); do "
        'tmux detach-client -t "$tty" 2>/dev/null; done; fi; '
        f"exec tmux new-session -A -s {name} -c {workdir} bash -l; "
        "fi; "
        "exec bash -l"
    )


def build_shell_command(container: str, cwd: str, session: str | None = None) -> list[str]:
    """Return the docker command that opens a login shell in the container."""
    command = [
        docker_executable(),
        "exec",
        "-i",
        "-t",
        "-e",
        "TERM=xterm-256color",
        "-e",
        "COLORTERM=truecolor",
        "-w",
        cwd,
        container,
        "bash",
        "-l",
    ]
    if session and SESSION_PATTERN.match(session):
        command[-2:] = ["bash", "-lc", build_attach_script(cwd, session)]
    return command


def set_window_size(fd: int, cols: int, rows: int) -> None:
    """Apply a terminal window size to a PTY file descriptor."""
    packed = struct.pack("HHHH", rows, cols, 0, 0)
    fcntl.ioctl(fd, termios.TIOCSWINSZ, packed)


def _handle_control(fd: int, message: str, pid: int | None = None) -> None:
    """Apply a JSON control frame, falling back to typing literal text."""
    try:
        payload: Any = json.loads(message)
    except json.JSONDecodeError:
        os.write(fd, message.encode("utf-8"))
        return

    if isinstance(payload, dict) and payload.get("type") == "resize":
        try:
            cols = max(20, min(400, int(payload.get("cols", DEFAULT_COLS))))
            rows = max(5, min(200, int(payload.get("rows", DEFAULT_ROWS))))
        except (TypeError, ValueError):
            return
        try:
            set_window_size(fd, cols, rows)
        except OSError:
            logger.debug("pty_resize_failed", fd=fd)
            return
        # The exec client has no controlling terminal, so the kernel does not
        # deliver SIGWINCH for us; forwarding it makes the client notice the new
        # size and resize the container's TTY (and with it the tmux pane).
        if pid:
            try:
                os.killpg(pid, signal.SIGWINCH)
            except (ProcessLookupError, PermissionError):
                logger.debug("pty_resize_signal_failed", pid=pid)


async def _tmux(container: str, *args: str) -> int | None:
    """Run a tmux command in the container; None when it cannot be run."""
    try:
        process = await asyncio.create_subprocess_exec(
            docker_executable(),
            "exec",
            container,
            "tmux",
            *args,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
    except (FileNotFoundError, OSError) as exc:
        logger.info("tmux_command_failed", args=list(args), error=str(exc))
        return None
    try:
        return await asyncio.wait_for(process.wait(), timeout=10)
    except TimeoutError:
        process.kill()
        await process.wait()
        return None


async def session_exists(container: str, session: str) -> bool:
    """Report whether the terminal's tmux session is still alive."""
    if not SESSION_PATTERN.match(session):
        return False
    return await _tmux(container, "has-session", "-t", session_name(session)) == 0


async def kill_session(container: str, session: str) -> None:
    """Terminate a tmux session so closing a tab does not orphan its processes."""
    if not SESSION_PATTERN.match(session):
        return
    await _tmux(container, "kill-session", "-t", session_name(session))


async def _stop_process(process: asyncio.subprocess.Process) -> None:
    """Terminate the docker exec client, escalating to a kill."""
    if process.returncode is not None:
        return
    try:
        process.terminate()
    except ProcessLookupError:
        return
    try:
        await asyncio.wait_for(process.wait(), timeout=5)
    except TimeoutError:
        try:
            process.kill()
        except ProcessLookupError:
            return
        await process.wait()


async def stream_shell(
    websocket: WebSocket,
    *,
    container: str,
    cwd: str,
    cols: int = DEFAULT_COLS,
    rows: int = DEFAULT_ROWS,
    session: str | None = None,
) -> None:
    """Bridge a devbox shell to an accepted WebSocket until either side closes.

    With a session name the shell lives in tmux and survives disconnect, so a
    page reload resumes the same process and history.
    """
    # The browser paints the tab's last screen before connecting. If the session
    # is gone (devbox reset, container recreated) nothing would overwrite that
    # preview, so clear it before the new shell draws.
    if session and not await session_exists(container, session):
        try:
            await websocket.send_bytes(CLEAR_SCREEN)
        except Exception:  # pragma: no cover - client already gone
            logger.debug("shell_preview_clear_failed")

    master_fd, slave_fd = pty.openpty()
    set_window_size(master_fd, cols, rows)
    os.set_blocking(master_fd, False)

    try:
        process = await asyncio.create_subprocess_exec(
            *build_shell_command(container, cwd, session),
            stdin=slave_fd,
            stdout=slave_fd,
            stderr=slave_fd,
            close_fds=True,
            start_new_session=True,
        )
    except (FileNotFoundError, OSError) as exc:
        os.close(master_fd)
        os.close(slave_fd)
        logger.error("shell_spawn_failed", error=str(exc))
        await websocket.close(code=1011, reason="Could not start the devbox shell")
        return

    os.close(slave_fd)
    loop = asyncio.get_running_loop()
    output: asyncio.Queue[bytes | None] = asyncio.Queue()

    def _on_readable() -> None:
        try:
            chunk = os.read(master_fd, READ_SIZE)
        except BlockingIOError:
            return
        except OSError:
            chunk = b""
        if not chunk:
            loop.remove_reader(master_fd)
            output.put_nowait(None)
            return
        output.put_nowait(chunk)

    loop.add_reader(master_fd, _on_readable)

    async def pump_output() -> None:
        while True:
            chunk = await output.get()
            if chunk is None:
                return
            await websocket.send_bytes(chunk)

    async def pump_input() -> None:
        while True:
            message = await websocket.receive()
            if message.get("type") == "websocket.disconnect":
                return
            data = message.get("bytes")
            if data:
                os.write(master_fd, data)
                continue
            text = message.get("text")
            if text:
                _handle_control(master_fd, text, process.pid)

    tasks = [asyncio.create_task(pump_output()), asyncio.create_task(pump_input())]
    try:
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    except Exception as exc:  # pragma: no cover - transport failures
        logger.debug("shell_stream_ended", error=str(exc))
    finally:
        loop.remove_reader(master_fd)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await _stop_process(process)
        os.close(master_fd)
