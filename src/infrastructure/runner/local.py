"""Local Process Runner implementing HostBackend for development and local testing."""

from __future__ import annotations

import asyncio
import errno
import os
import pty
import struct
import tempfile
import termios
from collections.abc import AsyncGenerator

from src.infrastructure.runner.base import (
    DevServerInfo,
    GitDiffInfo,
    HostBackend,
    HostInfo,
    LaneInfo,
)


class LocalPTYSession:
    def __init__(self, pty_id: str, master_fd: int, pid: int, lane_id: str) -> None:
        self.pty_id = pty_id
        self.master_fd = master_fd
        self.pid = pid
        self.lane_id = lane_id
        self.is_running = True
        self.output_queue: asyncio.Queue[str] = asyncio.Queue()
        self.reader_task: asyncio.Task | None = None


class LocalProcessRunner(HostBackend):
    """Local process/filesystem runner with real OS PTYs and Git worktree handling."""

    def __init__(self, base_workdir: str | None = None) -> None:
        self.base_workdir = base_workdir or os.path.join(tempfile.gettempdir(), "congruence_runners")
        os.makedirs(self.base_workdir, exist_ok=True)
        self._hosts: dict[str, HostInfo] = {}
        self._lanes: dict[str, LaneInfo] = {}
        self._ptys: dict[str, LocalPTYSession] = {}
        self._dev_servers: dict[str, asyncio.subprocess.Process] = {}
        self._dev_server_info: dict[str, DevServerInfo] = {}

    async def create_host(self, project_id: str, size: str = "standard") -> HostInfo:
        import nanoid
        host_id = f"host_{nanoid.generate(size=12)}"
        host_dir = os.path.join(self.base_workdir, host_id)
        os.makedirs(host_dir, exist_ok=True)
        
        # Initialize bare repository or repo workspace
        repo_dir = os.path.join(host_dir, "repo")
        os.makedirs(repo_dir, exist_ok=True)
        
        proc = await asyncio.create_subprocess_exec(
            "git", "init", "-b", "main", repo_dir,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await proc.communicate()

        info = HostInfo(
            host_id=host_id,
            project_id=project_id,
            backend_type="process_local",
            state="awake",
            provider_instance_id=f"local-proc-{host_id}",
        )
        self._hosts[host_id] = info
        return info

    async def wake_host(self, host_id: str) -> HostInfo:
        if host_id not in self._hosts:
            self._hosts[host_id] = HostInfo(
                host_id=host_id,
                project_id=f"proj_{host_id}",
                backend_type="process_local",
                state="awake",
            )
        self._hosts[host_id].state = "awake"
        return self._hosts[host_id]

    async def sleep_host(self, host_id: str) -> HostInfo:
        if host_id not in self._hosts:
            self._hosts[host_id] = HostInfo(
                host_id=host_id,
                project_id=f"proj_{host_id}",
                backend_type="process_local",
                state="asleep",
            )
        # Stop any running dev servers
        for lane_id, srv in list(self._dev_servers.items()):
            lane = self._lanes.get(lane_id)
            if lane and lane.host_id == host_id:
                await self.stop_dev_server(lane_id)
        self._hosts[host_id].state = "asleep"
        return self._hosts[host_id]

    async def get_host_state(self, host_id: str) -> str:
        if host_id in self._hosts:
            return self._hosts[host_id].state
        return "asleep"

    async def create_lane(
        self, host_id: str, branch_name: str, is_pair_lane: bool = False
    ) -> LaneInfo:
        import nanoid
        lane_id = f"lane_{nanoid.generate(size=12)}"
        host_dir = os.path.join(self.base_workdir, host_id)
        repo_dir = os.path.join(host_dir, "repo")
        worktree_path = os.path.join(host_dir, "lanes", lane_id)
        os.makedirs(os.path.dirname(worktree_path), exist_ok=True)

        if is_pair_lane:
            lane_path = repo_dir
        else:
            lane_path = worktree_path
            os.makedirs(lane_path, exist_ok=True)

        info = LaneInfo(
            lane_id=lane_id,
            host_id=host_id,
            branch_name=branch_name,
            worktree_path=lane_path,
            is_pair_lane=is_pair_lane,
            status="ready",
        )
        self._lanes[lane_id] = info
        return info

    async def clone_repository(
        self, host_id: str, repo_url: str, token: str | None = None
    ) -> bool:
        """Clone a remote Git repository into the host's base repo directory."""
        import shutil
        host_dir = os.path.join(self.base_workdir, host_id)
        repo_dir = os.path.join(host_dir, "repo")
        os.makedirs(host_dir, exist_ok=True)
        if os.path.exists(repo_dir):
            shutil.rmtree(repo_dir, ignore_errors=True)

        target_url = repo_url
        if token and "github.com" in repo_url:
            clean_url = repo_url.replace("https://", "").replace("http://", "")
            target_url = f"https://x-access-token:{token}@{clean_url}"

        proc = await asyncio.create_subprocess_exec(
            "git", "clone", target_url, repo_dir,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, _ = await proc.communicate()
        return proc.returncode == 0

    async def push_worktree_branch(
        self, lane_id: str, remote_name: str = "origin"
    ) -> tuple[bool, str]:
        """Push the lane's worktree branch to the upstream remote."""
        lane = self._lanes.get(lane_id)
        if not lane:
            return False, "Lane not found"
        proc = await asyncio.create_subprocess_exec(
            "git", "push", "-u", remote_name, lane.branch_name,
            cwd=lane.worktree_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        out, err = await proc.communicate()
        return proc.returncode == 0, (out or err).decode("utf-8", errors="replace")

    async def spawn_pty(
        self,
        lane_id: str,
        command: list[str] | str,
        env: dict[str, str] | None = None,
        cols: int = 80,
        rows: int = 24,
    ) -> str:
        import nanoid
        pty_id = f"pty_{nanoid.generate(size=12)}"
        lane = self._lanes.get(lane_id)
        cwd = lane.worktree_path if lane else self.base_workdir

        master_fd, slave_fd = pty.openpty()
        
        # Set initial terminal size
        winsize = struct.pack("HHHH", rows, cols, 0, 0)
        import fcntl
        fcntl.ioctl(master_fd, termios.TIOCSWINSZ, winsize)

        cmd_args = command if isinstance(command, list) else ["/bin/sh", "-c", command]

        merged_env = os.environ.copy()
        if env:
            merged_env.update(env)
        merged_env["TERM"] = "xterm-256color"
        merged_env["COLORTERM"] = "truecolor"

        pid = os.fork()
        if pid == 0:
            # Child process
            os.close(master_fd)
            os.setsid()
            os.dup2(slave_fd, 0)
            os.dup2(slave_fd, 1)
            os.dup2(slave_fd, 2)
            if slave_fd > 2:
                os.close(slave_fd)
            try:
                os.chdir(cwd)
                os.execvpe(cmd_args[0], cmd_args, merged_env)
            except Exception as e:
                os.write(2, f"Failed to exec: {e}\n".encode())
                os._exit(1)

        # Parent process
        os.close(slave_fd)
        
        # Set non-blocking on master_fd
        import fcntl
        flags = fcntl.fcntl(master_fd, fcntl.F_GETFL)
        fcntl.fcntl(master_fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)

        session = LocalPTYSession(pty_id, master_fd, pid, lane_id)
        
        # Background reader task
        async def _read_loop():
            while session.is_running:
                try:
                    await asyncio.sleep(0.01)
                    try:
                        data = os.read(session.master_fd, 4096)
                        if data:
                            await session.output_queue.put(data.decode("utf-8", errors="replace"))
                        else:
                            break
                    except (BlockingIOError, InterruptedError):
                        continue
                    except OSError as e:
                        if e.errno == errno.EIO:  # EIO indicates EOF on PTY in Linux/macOS
                            break
                        raise
                except asyncio.CancelledError:
                    break
                except Exception:
                    break
            session.is_running = False

        session.reader_task = asyncio.create_task(_read_loop())
        self._ptys[pty_id] = session
        return pty_id

    async def write_pty(self, pty_id: str, data: str | bytes) -> None:
        session = self._ptys.get(pty_id)
        if not session or not session.is_running:
            return
        payload = data.encode("utf-8") if isinstance(data, str) else data
        try:
            os.write(session.master_fd, payload)
        except OSError:
            pass

    async def resize_pty(self, pty_id: str, cols: int, rows: int) -> None:
        session = self._ptys.get(pty_id)
        if not session or not session.is_running:
            return
        import fcntl
        winsize = struct.pack("HHHH", rows, cols, 0, 0)
        try:
            fcntl.ioctl(session.master_fd, termios.TIOCSWINSZ, winsize)
        except OSError:
            pass

    async def read_pty_stream(self, pty_id: str) -> AsyncGenerator[str, None]:
        session = self._ptys.get(pty_id)
        if not session:
            return
        while session.is_running or not session.output_queue.empty():
            try:
                chunk = await asyncio.wait_for(session.output_queue.get(), timeout=0.2)
                yield chunk
            except TimeoutError:
                if not session.is_running and session.output_queue.empty():
                    break
                continue

    async def terminate_pty(self, pty_id: str) -> None:
        session = self._ptys.pop(pty_id, None)
        if session:
            session.is_running = False
            if session.reader_task:
                session.reader_task.cancel()
            try:
                os.close(session.master_fd)
            except OSError:
                pass
            try:
                os.kill(session.pid, 9)
                os.waitpid(session.pid, os.WNOHANG)
            except OSError:
                pass

    async def start_dev_server(
        self, lane_id: str, command: str, port: int = 3000
    ) -> DevServerInfo:
        lane = self._lanes.get(lane_id)
        cwd = lane.worktree_path if lane else self.base_workdir
        
        proc = await asyncio.create_subprocess_shell(
            command,
            cwd=cwd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        self._dev_servers[lane_id] = proc
        info = DevServerInfo(
            lane_id=lane_id,
            port=port,
            pid=proc.pid,
            is_running=True,
            url=f"http://localhost:{port}",
        )
        self._dev_server_info[lane_id] = info
        return info

    async def stop_dev_server(self, lane_id: str) -> None:
        proc = self._dev_servers.pop(lane_id, None)
        if proc:
            try:
                proc.terminate()
                await asyncio.wait_for(proc.wait(), timeout=2.0)
            except (TimeoutError, ProcessLookupError):
                try:
                    proc.kill()
                except ProcessLookupError:
                    pass
        if lane_id in self._dev_server_info:
            self._dev_server_info[lane_id].is_running = False

    async def get_diff(self, lane_id: str) -> GitDiffInfo:
        lane = self._lanes.get(lane_id)
        if not lane or not os.path.exists(lane.worktree_path):
            return GitDiffInfo(
                lane_id=lane_id,
                branch_name=lane.branch_name if lane else "main",
                files_changed=0,
                insertions=0,
                deletions=0,
                diff_text="",
            )

        proc = await asyncio.create_subprocess_exec(
            "git", "diff", "--stat",
            cwd=lane.worktree_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        diff_text = stdout.decode("utf-8", errors="replace")
        
        return GitDiffInfo(
            lane_id=lane_id,
            branch_name=lane.branch_name,
            files_changed=1 if diff_text else 0,
            insertions=1 if diff_text else 0,
            deletions=0,
            diff_text=diff_text,
        )
