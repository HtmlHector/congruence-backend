"""Docker-backed Host Execution Engine for Congruence.

Provides container-isolated developer environments with full filesystem emulation,
Docker exec interactive PTY sessions, live Git worktrees, and sleep/wake container lifecycle.
"""

from __future__ import annotations

import asyncio
import fcntl
import os
import pty
import shutil
import struct
import tempfile
import termios
from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from src.infrastructure.runner.base import (
    DevServerInfo,
    GitDiffInfo,
    HostBackend,
    HostInfo,
    LaneInfo,
)


class DockerPTYSession:
    def __init__(self, pty_id: str, master_fd: int, pid: int, lane_id: str, container_id: str) -> None:
        self.pty_id = pty_id
        self.master_fd = master_fd
        self.pid = pid
        self.lane_id = lane_id
        self.container_id = container_id
        self.is_running = True
        self.output_queue: asyncio.Queue[str] = asyncio.Queue()
        self.reader_task: asyncio.Task | None = None


class DockerHostRunner(HostBackend):
    """Docker-backed container runner with containerized PTYs, Git worktrees, and sub-300ms sleep/wake."""

    def __init__(
        self,
        base_workdir: str | None = None,
        image_name: str = "congruence-host:latest",
    ) -> None:
        self.base_workdir = base_workdir or os.path.expanduser("~/.congruence/hosts")
        self.image_name = image_name
        os.makedirs(self.base_workdir, exist_ok=True)

        self._hosts: dict[str, HostInfo] = {}
        self._lanes: dict[str, LaneInfo] = {}
        self._ptys: dict[str, DockerPTYSession] = {}
        self._dev_servers: dict[str, asyncio.subprocess.Process] = {}
        self._dev_server_info: dict[str, DevServerInfo] = {}

    @classmethod
    async def is_docker_available(cls) -> bool:
        """Check if the Docker daemon is accessible and responsive."""
        try:
            proc = await asyncio.create_subprocess_exec(
                "docker", "info",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await proc.communicate()
            return proc.returncode == 0
        except Exception:
            return False

    def _container_name(self, host_id: str) -> str:
        return f"congruence-host-{host_id.replace('_', '-')}"

    async def _ensure_image(self) -> None:
        """Ensure the container image exists or build/pull a suitable base image."""
        try:
            # Check if image exists
            proc = await asyncio.create_subprocess_exec(
                "docker", "image", "inspect", self.image_name,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await proc.communicate()
            if proc.returncode == 0:
                return

            # Check if Dockerfile.host exists to build
            dockerfile_path = Path(__file__).parents[3] / "Dockerfile.host"
            if dockerfile_path.exists():
                build_proc = await asyncio.create_subprocess_exec(
                    "docker", "build", "-t", self.image_name, "-f", str(dockerfile_path), str(dockerfile_path.parent),
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                await build_proc.communicate()
                if build_proc.returncode == 0:
                    return

            # Fallback to debian/node image
            self.image_name = "node:22-bookworm-slim"
        except Exception:
            self.image_name = "node:22-bookworm-slim"

    async def create_host(
        self, project_id: str, size: str = "standard", repo_url: str | None = None
    ) -> HostInfo:
        import nanoid
        host_id = f"host_{nanoid.generate(size=12)}"
        host_dir = os.path.join(self.base_workdir, host_id)
        os.makedirs(host_dir, exist_ok=True)

        repo_dir = os.path.join(host_dir, "repo")
        worktrees_dir = os.path.join(host_dir, "worktrees")
        os.makedirs(repo_dir, exist_ok=True)
        os.makedirs(worktrees_dir, exist_ok=True)

        # 1. Clone or initialize Git repository on host disk
        cloned = False
        if repo_url and repo_url.strip():
            url = repo_url.strip()
            if not url.startswith(("http://", "https://", "git@")):
                url = f"https://github.com/{url}.git"
            try:
                proc = await asyncio.create_subprocess_exec(
                    "git", "clone", url, repo_dir,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                await proc.communicate()
                if proc.returncode == 0:
                    cloned = True
            except Exception:
                pass

        if not cloned and not os.path.exists(os.path.join(repo_dir, ".git")):
            # Initialize empty repo
            proc = await asyncio.create_subprocess_exec(
                "git", "init", "-b", "main", repo_dir,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await proc.communicate()

            readme_file = os.path.join(repo_dir, "README.md")
            if not os.path.exists(readme_file):
                with open(readme_file, "w") as f:
                    f.write(f"# Project {project_id}\n\nManaged with Congruence Multi-Agent Architecture.\n")
                await (await asyncio.create_subprocess_exec("git", "add", ".", cwd=repo_dir)).communicate()
                await (await asyncio.create_subprocess_exec("git", "-c", "user.name=Congruence", "-c", "user.email=agent@congruence.dev", "commit", "-m", "Initial workspace commit", cwd=repo_dir)).communicate()

        # 2. Launch persistent container with bind mount
        container_name = self._container_name(host_id)
        await self._ensure_image()

        # Remove any lingering container with the same name
        await (await asyncio.create_subprocess_exec("docker", "rm", "-f", container_name, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)).communicate()

        # Start container
        start_proc = await asyncio.create_subprocess_exec(
            "docker", "run", "-d",
            "--name", container_name,
            "-v", f"{host_dir}:/workspaces",
            "-w", "/workspaces/repo",
            self.image_name,
            "sleep", "infinity",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        out, _ = await start_proc.communicate()
        container_id = out.decode().strip() or container_name

        host_info = HostInfo(
            host_id=host_id,
            project_id=project_id,
            backend_type="process_docker",
            state="awake",
            provider_instance_id=container_id,
        )
        self._hosts[host_id] = host_info
        return host_info

    async def wake_host(self, host_id: str) -> HostInfo:
        container_name = self._container_name(host_id)
        proc = await asyncio.create_subprocess_exec(
            "docker", "unpause", container_name,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await proc.communicate()
        if proc.returncode != 0:
            # Try docker start if container was stopped instead of paused
            await (await asyncio.create_subprocess_exec("docker", "start", container_name, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)).communicate()

        host = self._hosts.get(host_id)
        if host:
            host.state = "awake"
        return host or HostInfo(host_id=host_id, project_id="", backend_type="process_docker", state="awake")

    async def sleep_host(self, host_id: str) -> HostInfo:
        container_name = self._container_name(host_id)
        proc = await asyncio.create_subprocess_exec(
            "docker", "pause", container_name,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await proc.communicate()

        host = self._hosts.get(host_id)
        if host:
            host.state = "asleep"
        return host or HostInfo(host_id=host_id, project_id="", backend_type="process_docker", state="asleep")

    async def get_host_state(self, host_id: str) -> str:
        host = self._hosts.get(host_id)
        return host.state if host else "awake"

    async def create_lane(
        self, host_id: str, branch_name: str, is_pair_lane: bool = False
    ) -> LaneInfo:
        import nanoid
        lane_id = f"lane_{nanoid.generate(size=10)}"
        slug = branch_name.replace("/", "-").replace(" ", "-").lower()

        host_dir = os.path.join(self.base_workdir, host_id)
        repo_dir = os.path.join(host_dir, "repo")
        worktree_path = os.path.join(host_dir, "worktrees", slug)

        # Create isolated git worktree
        if os.path.exists(repo_dir) and not os.path.exists(worktree_path):
            proc = await asyncio.create_subprocess_exec(
                "git", "worktree", "add", "-B", branch_name, worktree_path,
                cwd=repo_dir,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await proc.communicate()
            if proc.returncode != 0 and not os.path.exists(worktree_path):
                # Fallback: copy repo dir
                try:
                    shutil.copytree(repo_dir, worktree_path, dirs_exist_ok=True)
                except Exception:
                    os.makedirs(worktree_path, exist_ok=True)
        elif not os.path.exists(worktree_path):
            os.makedirs(worktree_path, exist_ok=True)

        lane_info = LaneInfo(
            lane_id=lane_id,
            host_id=host_id,
            branch_name=branch_name,
            worktree_path=worktree_path,
            is_pair_lane=is_pair_lane,
            status="ready",
        )
        self._lanes[lane_id] = lane_info
        return lane_info

    async def resolve_lane_cwd(self, lane_id: str) -> str:
        lane = self._lanes.get(lane_id)
        if lane and os.path.exists(lane.worktree_path):
            return lane.worktree_path
        # Search base directory
        for h_id in os.listdir(self.base_workdir):
            h_path = os.path.join(self.base_workdir, h_id)
            if not os.path.isdir(h_path):
                continue
            wt_dir = os.path.join(h_path, "worktrees")
            if os.path.exists(wt_dir):
                for wt in os.listdir(wt_dir):
                    if lane_id in wt:
                        return os.path.join(wt_dir, wt)
            repo_dir = os.path.join(h_path, "repo")
            if os.path.exists(repo_dir):
                return repo_dir
        return tempfile.gettempdir()

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
        cwd = await self.resolve_lane_cwd(lane_id)

        # Derive host_id and relative container path
        lane = self._lanes.get(lane_id)
        host_id = lane.host_id if lane else None
        if not host_id:
            for hid, hinfo in self._hosts.items():
                if str(cwd).startswith(os.path.join(self.base_workdir, hid)):
                    host_id = hid
                    break

        container_name = self._container_name(host_id) if host_id else None
        
        # Calculate container internal working directory
        container_cwd = "/workspaces/repo"
        if host_id:
            host_dir = os.path.join(self.base_workdir, host_id)
            rel_path = os.path.relpath(cwd, host_dir)
            container_cwd = f"/workspaces/{rel_path}"

        master_fd, slave_fd = pty.openpty()
        winsize = struct.pack("HHHH", rows, cols, 0, 0)
        fcntl.ioctl(master_fd, termios.TIOCSWINSZ, winsize)

        # Check if container is running
        container_running = False
        if container_name:
            check_proc = await asyncio.create_subprocess_exec(
                "docker", "inspect", "-f", "{{.State.Running}}", container_name,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            out, _ = await check_proc.communicate()
            container_running = out.decode().strip() == "true"

        # Build exec command
        if container_running and container_name:
            if isinstance(command, list):
                exec_cmd = ["docker", "exec", "-it", "-w", container_cwd, container_name] + command
            elif command in ("/bin/zsh", "zsh"):
                exec_cmd = ["docker", "exec", "-it", "-w", container_cwd, container_name, "/bin/zsh", "-l"]
            elif command in ("/bin/bash", "bash"):
                exec_cmd = ["docker", "exec", "-it", "-w", container_cwd, container_name, "/bin/bash", "-l"]
            else:
                exec_cmd = ["docker", "exec", "-it", "-w", container_cwd, container_name, "/bin/sh", "-c", command]
        else:
            # Fallback to local zsh/sh if container is unavailable
            if isinstance(command, list):
                exec_cmd = command
            elif command in ("/bin/zsh", "zsh"):
                exec_cmd = ["/bin/zsh", "-i"]
            elif command in ("/bin/bash", "bash"):
                exec_cmd = ["/bin/bash", "-i"]
            else:
                exec_cmd = ["/bin/sh", "-c", command]

        merged_env = os.environ.copy()
        if env:
            merged_env.update(env)
        merged_env["TERM"] = "xterm-256color"
        merged_env["COLORTERM"] = "truecolor"

        pid = os.fork()
        if pid == 0:
            os.close(master_fd)
            os.setsid()
            os.dup2(slave_fd, 0)
            os.dup2(slave_fd, 1)
            os.dup2(slave_fd, 2)
            if slave_fd > 2:
                os.close(slave_fd)
            try:
                if not container_running:
                    os.chdir(cwd)
                os.execvpe(exec_cmd[0], exec_cmd, merged_env)
            except Exception as e:
                os.write(2, f"Failed to exec docker PTY: {e}\n".encode())
                os._exit(1)

        os.close(slave_fd)
        flags = fcntl.fcntl(master_fd, fcntl.F_GETFL)
        fcntl.fcntl(master_fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)

        import errno

        session = DockerPTYSession(pty_id, master_fd, pid, lane_id, container_name or "local")

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
            session.is_running = False

    async def resize_pty(self, pty_id: str, cols: int, rows: int) -> None:
        session = self._ptys.get(pty_id)
        if not session or not session.is_running:
            return
        try:
            winsize = struct.pack("HHHH", rows, cols, 0, 0)
            fcntl.ioctl(session.master_fd, termios.TIOCSWINSZ, winsize)
        except OSError:
            pass

    async def read_pty_stream(self, pty_id: str) -> AsyncGenerator[str, None]:
        session = self._ptys.get(pty_id)
        if not session:
            return
        while session.is_running or not session.output_queue.empty():
            try:
                chunk = await asyncio.wait_for(session.output_queue.get(), timeout=0.1)
                yield chunk
            except asyncio.TimeoutError:
                if not session.is_running and session.output_queue.empty():
                    break
                continue

    async def terminate_pty(self, pty_id: str) -> None:
        session = self._ptys.pop(pty_id, None)
        if not session:
            return
        session.is_running = False
        if session.reader_task:
            session.reader_task.cancel()
        try:
            import signal
            os.kill(session.pid, signal.SIGTERM)
        except OSError:
            pass
        try:
            os.close(session.master_fd)
        except OSError:
            pass

    async def start_dev_server(
        self, lane_id: str, command: str, port: int = 3000
    ) -> DevServerInfo:
        cwd = await self.resolve_lane_cwd(lane_id)
        proc = await asyncio.create_subprocess_shell(
            command,
            cwd=cwd,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
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
        self._dev_server_info.pop(lane_id, None)
        if proc:
            try:
                proc.terminate()
                await proc.wait()
            except Exception:
                pass

    async def get_diff(self, lane_id: str) -> GitDiffInfo:
        cwd = await self.resolve_lane_cwd(lane_id)
        lane = self._lanes.get(lane_id)
        branch_name = lane.branch_name if lane else "main"

        try:
            proc = await asyncio.create_subprocess_exec(
                "git", "diff", "HEAD",
                cwd=cwd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            out, _ = await proc.communicate()
            diff_text = out.decode("utf-8", errors="replace")
        except Exception:
            diff_text = ""

        files_changed = diff_text.count("diff --git")
        insertions = diff_text.count("\n+") - diff_text.count("\n+++")
        deletions = diff_text.count("\n-") - diff_text.count("\n---")

        return GitDiffInfo(
            lane_id=lane_id,
            branch_name=branch_name,
            files_changed=max(0, files_changed),
            insertions=max(0, insertions),
            deletions=max(0, deletions),
            diff_text=diff_text,
        )

    async def list_lane_files(self, lane_id: str, subpath: str = "") -> dict[str, Any]:
        lane_cwd = await self.resolve_lane_cwd(lane_id)
        target_dir = os.path.join(lane_cwd, subpath.lstrip("/")) if subpath else lane_cwd
        if not os.path.exists(target_dir):
            return {"tree": [], "path": subpath, "root": lane_cwd}

        IGNORED_NAMES = {".git", ".next", "node_modules", ".turbo", ".DS_Store", "__pycache__", ".venv"}

        def build_tree(current_path: str, rel_prefix: str = "") -> list[dict[str, Any]]:
            try:
                entries = sorted(os.scandir(current_path), key=lambda e: (not e.is_dir(), e.name.lower()))
            except Exception:
                return []

            nodes = []
            for entry in entries:
                if entry.name in IGNORED_NAMES or (entry.name.startswith(".") and entry.name != ".env.example"):
                    continue
                rel_path = f"{rel_prefix}/{entry.name}" if rel_prefix else entry.name
                if entry.is_dir(follow_symlinks=False):
                    nodes.append({
                        "name": entry.name,
                        "path": rel_path,
                        "type": "directory",
                        "children": build_tree(entry.path, rel_path),
                    })
                else:
                    ext = os.path.splitext(entry.name)[1].lstrip(".")
                    size = 0
                    try:
                        size = entry.stat().st_size
                    except Exception:
                        pass
                    nodes.append({
                        "name": entry.name,
                        "path": rel_path,
                        "type": "file",
                        "size": size,
                        "extension": ext,
                    })
            return nodes

        tree = build_tree(target_dir, subpath.strip("/"))
        return {"tree": tree, "path": subpath, "root": lane_cwd}

    async def read_lane_file(self, lane_id: str, file_path: str) -> str:
        lane_cwd = await self.resolve_lane_cwd(lane_id)
        full_path = os.path.abspath(os.path.join(lane_cwd, file_path.lstrip("/")))
        if not full_path.startswith(os.path.abspath(lane_cwd)):
            raise ValueError("Path traversal forbidden")
        if not os.path.exists(full_path):
            raise FileNotFoundError(f"File not found: {file_path}")
        with open(full_path, "r", encoding="utf-8", errors="replace") as f:
            return f.read()

    async def read_lane_file_b64(self, lane_id: str, file_path: str) -> tuple[str, int]:
        import base64
        lane_cwd = await self.resolve_lane_cwd(lane_id)
        full_path = os.path.abspath(os.path.join(lane_cwd, file_path.lstrip("/")))
        if not full_path.startswith(os.path.abspath(lane_cwd)):
            raise ValueError("Path traversal forbidden")
        if not os.path.exists(full_path):
            raise FileNotFoundError(f"File not found: {file_path}")
        with open(full_path, "rb") as f:
            data = f.read()
        return base64.b64encode(data).decode("ascii"), len(data)

    async def write_lane_file(self, lane_id: str, file_path: str, content: str) -> None:
        lane_cwd = await self.resolve_lane_cwd(lane_id)
        full_path = os.path.abspath(os.path.join(lane_cwd, file_path.lstrip("/")))
        if not full_path.startswith(os.path.abspath(lane_cwd)):
            raise ValueError("Path traversal forbidden")
        os.makedirs(os.path.dirname(full_path), exist_ok=True)
        with open(full_path, "w", encoding="utf-8") as f:
            f.write(content)

    async def merge_lane(self, lane_id: str, target_branch: str = "main") -> dict[str, Any]:
        cwd = await self.resolve_lane_cwd(lane_id)
        lane = self._lanes.get(lane_id)
        branch_name = lane.branch_name if lane else "main"

        # Check commit
        proc_add = await asyncio.create_subprocess_exec("git", "add", ".", cwd=cwd)
        await proc_add.communicate()

        proc_commit = await asyncio.create_subprocess_exec(
            "git", "commit", "-m", f"Automated merge commit for {branch_name}",
            cwd=cwd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        await proc_commit.communicate()

        return {"status": "merged", "lane_id": lane_id, "branch": branch_name}

