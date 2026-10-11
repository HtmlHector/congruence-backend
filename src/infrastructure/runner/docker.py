"""Docker-backed Host Execution Engine for Congruence.

Provides container-isolated developer environments with full filesystem emulation,
Docker exec interactive PTY sessions, live Git worktrees, and sleep/wake container lifecycle.
"""

from __future__ import annotations

import asyncio
import errno
import fcntl
import json
import os
import pty
import shutil
import struct
import termios
from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import structlog

from src.infrastructure.runner.base import (
    DevServerInfo,
    GitDiffInfo,
    HostBackend,
    HostInfo,
    LaneInfo,
)

logger = structlog.get_logger(__name__)


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

    async def _ensure_network(self) -> None:
        """Ensure the congruence_net Docker network bridge exists."""
        try:
            proc = await asyncio.create_subprocess_exec(
                "docker", "network", "create", "congruence_net",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await proc.communicate()
        except Exception:
            pass

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

        # 2. Launch persistent container with bind mount & bridge network
        container_name = self._container_name(host_id)
        await self._ensure_network()
        await self._ensure_image()

        cache_base = os.path.expanduser("~/.congruence/cache")
        pnpm_cache = os.path.join(cache_base, "pnpm")
        uv_cache = os.path.join(cache_base, "uv")
        os.makedirs(pnpm_cache, exist_ok=True)
        os.makedirs(uv_cache, exist_ok=True)

        # Remove any lingering container with the same name
        await (await asyncio.create_subprocess_exec("docker", "rm", "-f", container_name, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)).communicate()

        # Start container with shared global package cache mounts
        start_proc = await asyncio.create_subprocess_exec(
            "docker", "run", "-d",
            "--name", container_name,
            "--network", "congruence_net",
            "-p", "127.0.0.1::3000",
            "-p", "127.0.0.1::3001",
            "-p", "127.0.0.1::5173",
            "-p", "127.0.0.1::8000",
            "-p", "127.0.0.1::8080",
            "-v", f"{host_dir}:/workspaces",
            "-v", f"{pnpm_cache}:/root/.local/share/pnpm/store",
            "-v", f"{uv_cache}:/root/.cache/uv",
            "-w", "/workspaces/repo",
            "-e", "TERM=xterm-256color",
            "-e", "COLORTERM=truecolor",
            "-e", "PNPM_HOME=/root/.local/share/pnpm",
            "-e", "UV_CACHE_DIR=/root/.cache/uv",
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

    async def clone_repository(
        self, host_id: str, repo_url: str, token: str | None = None
    ) -> bool:
        """Clone a remote Git repository into the host's base repo directory."""
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
        cwd = await self.resolve_lane_cwd(lane_id)
        branch_name = "main"
        if lane_id in self._lanes:
            branch_name = self._lanes[lane_id].branch_name
        else:
            try:
                from sqlalchemy import select

                from src.contexts.congruence.models import Lane
                from src.infrastructure.database.session import AsyncSessionFactory
                async with AsyncSessionFactory() as session:
                    l = (await session.execute(select(Lane).where(Lane.id == lane_id))).scalar_one_or_none()
                    if l:
                        branch_name = l.branch_name
            except Exception:
                pass

        proc = await asyncio.create_subprocess_exec(
            "git", "push", "-u", remote_name, branch_name,
            cwd=cwd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        out, err = await proc.communicate()
        return proc.returncode == 0, (out or err).decode("utf-8", errors="replace")

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

    async def _resolve_lane_and_host(self, lane_id: str) -> tuple[str, str, str]:
        """Resolve host_id, host_worktree_path, and container_cwd for a given lane_id."""
        # 1. In-memory check
        lane = self._lanes.get(lane_id)
        if lane:
            host_id = lane.host_id
            host_dir = os.path.join(self.base_workdir, host_id)
            rel_path = os.path.relpath(lane.worktree_path, host_dir)
            return host_id, lane.worktree_path, f"/workspaces/{rel_path}"

        # 2. Database lookup
        try:
            from sqlalchemy import select

            from src.contexts.congruence.models import Host, Lane
            from src.infrastructure.database.session import AsyncSessionFactory
            async with AsyncSessionFactory() as session:
                target_lane = None
                if lane_id and lane_id not in ("main", "lane_main"):
                    target_lane = (await session.execute(select(Lane).where(Lane.id == lane_id))).scalar_one_or_none()

                if not target_lane:
                    # Pick latest active lane in DB
                    target_lane = (await session.execute(select(Lane).order_by(Lane.created_at.desc()))).scalars().first()

                if target_lane:
                    host_id = target_lane.host_id
                    host_dir = os.path.join(self.base_workdir, host_id)
                    lane_path = target_lane.worktree_path or os.path.join(host_dir, "worktrees", target_lane.slug)
                    if not os.path.exists(lane_path):
                        repo_dir = os.path.join(host_dir, "repo")
                        lane_path = repo_dir if os.path.exists(repo_dir) else host_dir
                    rel_path = os.path.relpath(lane_path, host_dir)
                    return host_id, lane_path, f"/workspaces/{rel_path}"

                # Check if any host exists
                host = (await session.execute(select(Host).order_by(Host.created_at.desc()))).scalars().first()
                if host:
                    host_dir = os.path.join(self.base_workdir, host.id)
                    repo_dir = os.path.join(host_dir, "repo")
                    return host.id, repo_dir, "/workspaces/repo"
        except Exception:
            pass

        # 3. Directory scan fallback
        if os.path.exists(self.base_workdir):
            for h_id in os.listdir(self.base_workdir):
                h_path = os.path.join(self.base_workdir, h_id)
                if not os.path.isdir(h_path) or h_id.startswith("."):
                    continue
                repo_dir = os.path.join(h_path, "repo")
                if os.path.exists(repo_dir):
                    return h_id, repo_dir, "/workspaces/repo"

        # 4. Default sandbox fallback
        sandbox_id = "sandbox"
        sandbox_dir = os.path.join(self.base_workdir, sandbox_id)
        repo_dir = os.path.join(sandbox_dir, "repo")
        os.makedirs(repo_dir, exist_ok=True)
        return sandbox_id, repo_dir, "/workspaces/repo"

    async def _ensure_container_running(self, host_id: str) -> str:
        """Ensure the container for host_id is initialized, running, and ready for exec."""
        container_name = self._container_name(host_id)
        host_dir = os.path.join(self.base_workdir, host_id)
        repo_dir = os.path.join(host_dir, "repo")
        worktrees_dir = os.path.join(host_dir, "worktrees")
        os.makedirs(repo_dir, exist_ok=True)
        os.makedirs(worktrees_dir, exist_ok=True)

        await self._ensure_network()
        await self._ensure_image()

        # Check container state
        check_proc = await asyncio.create_subprocess_exec(
            "docker", "inspect", "-f", "{{.State.Status}}", container_name,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        out, _ = await check_proc.communicate()
        status = out.decode().strip().lower()

        if status == "running":
            return container_name
        elif status == "paused":
            await (await asyncio.create_subprocess_exec("docker", "unpause", container_name, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)).communicate()
            return container_name
        elif status in ("exited", "created", "stopped"):
            await (await asyncio.create_subprocess_exec("docker", "start", container_name, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)).communicate()
            return container_name

        # Container does not exist; initialize and start
        await (await asyncio.create_subprocess_exec("docker", "rm", "-f", container_name, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)).communicate()

        if not os.path.exists(os.path.join(repo_dir, ".git")):
            await (await asyncio.create_subprocess_exec("git", "init", "-b", "main", repo_dir, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)).communicate()
            readme_path = os.path.join(repo_dir, "README.md")
            if not os.path.exists(readme_path):
                with open(readme_path, "w") as f:
                    f.write("# Congruence Containerized Workspace\n\nEmulated container microVM filesystem.\n")
                await (await asyncio.create_subprocess_exec("git", "add", ".", cwd=repo_dir, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)).communicate()
                await (await asyncio.create_subprocess_exec("git", "-c", "user.name=Congruence", "-c", "user.email=agent@congruence.dev", "commit", "-m", "Initial commit", cwd=repo_dir, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)).communicate()

        cache_base = os.path.expanduser("~/.congruence/cache")
        pnpm_cache = os.path.join(cache_base, "pnpm")
        uv_cache = os.path.join(cache_base, "uv")
        gemini_auth = os.path.expanduser("~/.congruence/auth/gemini/antigravity-cli")
        os.makedirs(pnpm_cache, exist_ok=True)
        os.makedirs(uv_cache, exist_ok=True)
        os.makedirs(gemini_auth, exist_ok=True)

        start_proc = await asyncio.create_subprocess_exec(
            "docker", "run", "-d",
            "--name", container_name,
            "--network", "congruence_net",
            "-p", "127.0.0.1::3000",
            "-p", "127.0.0.1::3001",
            "-p", "127.0.0.1::5173",
            "-p", "127.0.0.1::8000",
            "-p", "127.0.0.1::8080",
            "-v", f"{host_dir}:/workspaces",
            "-v", f"{pnpm_cache}:/root/.local/share/pnpm/store",
            "-v", f"{uv_cache}:/root/.cache/uv",
            "-v", f"{gemini_auth}:/root/.gemini/antigravity-cli",
            "-w", "/workspaces/repo",
            "-e", "TERM=xterm-256color",
            "-e", "COLORTERM=truecolor",
            "-e", "PNPM_HOME=/root/.local/share/pnpm",
            "-e", "UV_CACHE_DIR=/root/.cache/uv",
            self.image_name,
            "sleep", "infinity",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await start_proc.communicate()
        return container_name

    async def resolve_lane_cwd(self, lane_id: str) -> str:
        _, lane_path, _ = await self._resolve_lane_and_host(lane_id)
        return lane_path

    async def _sync_vault_secrets_to_container(self, container_name: str, lane_id: str) -> dict[str, str]:
        """Fetch active decrypted secrets from Postgres and inject them into container environment and CLI configs."""
        injected_env: dict[str, str] = {}
        try:
            from sqlalchemy import select

            from src.contexts.congruence.models import Host, Lane, OrganizationMember, Project
            from src.infrastructure.database.session import AsyncSessionFactory
            from src.infrastructure.security.vault import resolve_effective_secrets

            async with AsyncSessionFactory() as session:
                lane_stmt = select(Lane).where(Lane.id == lane_id)
                res = await session.execute(lane_stmt)
                lane = res.scalar_one_or_none()
                if lane:
                    host_stmt = select(Host).where(Host.id == lane.host_id)
                    host_res = await session.execute(host_stmt)
                    host = host_res.scalar_one_or_none()
                    if host:
                        proj_stmt = select(Project).where(Project.id == host.project_id)
                        proj_res = await session.execute(proj_stmt)
                        proj = proj_res.scalar_one_or_none()
                        if proj:
                            member_stmt = select(OrganizationMember).where(OrganizationMember.organization_id == proj.organization_id)
                            member_res = await session.execute(member_stmt)
                            member = member_res.scalar_one_or_none()
                            user_id = member.user_id if member else ""
                            # Fetch effective secrets for owner / user and project workspace
                            secrets = await resolve_effective_secrets(session, user_id, proj.id)
                            injected_env.update(secrets)

                            openai_key = secrets.get("OPENAI_API_KEY")
                            gemini_key = secrets.get("GEMINI_API_KEY") or secrets.get("GOOGLE_API_KEY")

                            # 1. Codex config
                            if openai_key:
                                codex_json = json.dumps({"authenticated": True, "provider": "openai", "token": openai_key, "model": "gpt-4o"})
                                await (await asyncio.create_subprocess_exec("docker", "exec", container_name, "sh", "-c", f"mkdir -p /root/.codex && echo '{codex_json}' > /root/.codex/auth.json", stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)).communicate()

                            # 2. Antigravity / Gemini config
                            if gemini_key:
                                agy_json = json.dumps({"authenticated": True, "api_key": gemini_key, "token": gemini_key, "provider": "google"})
                                await (await asyncio.create_subprocess_exec("docker", "exec", container_name, "sh", "-c", f"mkdir -p /root/.gemini/antigravity-cli && echo '{agy_json}' > /root/.gemini/antigravity-cli/auth.json", stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)).communicate()

                            # 3. Export to ~/.zshrc
                            export_lines = "".join([f"\nexport {k}='{v}'" for k, v in secrets.items()])
                            if export_lines:
                                await (await asyncio.create_subprocess_exec("docker", "exec", container_name, "sh", "-c", f"printf \"{export_lines}\" >> /root/.zshrc", stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)).communicate()
        except Exception as exc:
            logger.warning("failed_to_sync_vault_secrets_to_container", error=str(exc))

        return injected_env

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
        
        host_id, _, container_cwd = await self._resolve_lane_and_host(lane_id)
        container_name = await self._ensure_container_running(host_id)

        # Sync vault secrets into running container before exec
        vault_env = await self._sync_vault_secrets_to_container(container_name, lane_id)

        master_fd, slave_fd = pty.openpty()
        winsize = struct.pack("HHHH", rows, cols, 0, 0)
        fcntl.ioctl(master_fd, termios.TIOCSWINSZ, winsize)

        # Build exec command to run inside Docker container
        shell_target = "/bin/zsh"
        env_args = []
        for k, v in vault_env.items():
            env_args.extend(["-e", f"{k}={v}"])

        if isinstance(command, str) and command not in ("/bin/zsh", "zsh", "/bin/bash", "bash", "/bin/sh", "sh"):
            exec_cmd = ["docker", "exec", "-it", "-w", container_cwd, "-e", "TERM=xterm-256color", "-e", "COLORTERM=truecolor"] + env_args + [container_name, "/bin/sh", "-c", command]
        elif isinstance(command, list):
            exec_cmd = ["docker", "exec", "-it", "-w", container_cwd, "-e", "TERM=xterm-256color", "-e", "COLORTERM=truecolor"] + env_args + [container_name] + command
        else:
            exec_cmd = ["docker", "exec", "-it", "-w", container_cwd, "-e", "TERM=xterm-256color", "-e", "COLORTERM=truecolor"] + env_args + [container_name, shell_target, "-l"]

        merged_env = os.environ.copy()
        if env:
            merged_env.update(env)
        merged_env.update(vault_env)
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
                os.execvpe(exec_cmd[0], exec_cmd, merged_env)
            except Exception as e:
                os.write(2, f"Failed to exec docker PTY: {e}\n".encode())
                os._exit(1)

        os.close(slave_fd)
        flags = fcntl.fcntl(master_fd, fcntl.F_GETFL)
        fcntl.fcntl(master_fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)

        session = DockerPTYSession(pty_id, master_fd, pid, lane_id, container_name)

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
            except TimeoutError:
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

    async def get_preview_target(self, lane_id: str, port: int, subpath: str) -> str:
        """Return a validated host-side loopback URL for an exposed container port."""
        import ipaddress
        from urllib.parse import quote

        if lane_id not in self._lanes:
            raise ValueError("Lane is not registered in this runner")
        if not 1024 <= port <= 65535:
            raise ValueError("Preview port is invalid")
        if any(part == ".." for part in subpath.split("/")) or "\x00" in subpath:
            raise ValueError("Preview path is invalid")

        host_id = self._lanes[lane_id].host_id

        proc = await asyncio.create_subprocess_exec(
            "docker", "port", self._container_name(host_id), f"{port}/tcp",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        out, _ = await proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError("Preview service port is not published on the host container")
        mappings = out.decode("utf-8", errors="replace").splitlines()
        if not mappings:
            raise RuntimeError("Preview service port is not published on the host container")
        host, sep, host_port = mappings[0].rpartition(":")
        if not sep or not host_port.isdigit():
            raise RuntimeError("Docker returned an invalid port mapping")
        try:
            address = ipaddress.ip_address(host.strip("[]"))
        except ValueError as exc:
            raise RuntimeError("Docker returned a non-IP preview binding") from exc
        if not address.is_loopback:
            raise RuntimeError("Preview port is exposed beyond the local host")
        normalized = subpath.lstrip("/")
        return f"http://{address.compressed}:{host_port}/{quote(normalized, safe='/')}"

    async def start_dev_server(
        self, lane_id: str, command: str, port: int = 3000
    ) -> DevServerInfo:
        cwd = await self.resolve_lane_cwd(lane_id)
        lane = self._lanes.get(lane_id)
        host_id = lane.host_id if lane else None
        container_name = self._container_name(host_id) if host_id else None

        # Build container exec path
        container_cwd = "/workspaces/repo"
        if host_id:
            host_dir = os.path.join(self.base_workdir, host_id)
            rel_path = os.path.relpath(cwd, host_dir)
            container_cwd = f"/workspaces/{rel_path}"

        if container_name:
            proc = await asyncio.create_subprocess_exec(
                "docker", "exec", "-d", "-w", container_cwd, container_name,
                "sh", "-c", command,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
        else:
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

    async def detect_lane_ports(self, lane_id: str) -> list[int]:
        """Inspect active listening TCP ports inside container or host."""
        host_id, _, _ = await self._resolve_lane_and_host(lane_id)
        container_name = self._container_name(host_id)
        ports: set[int] = set()

        # 1. Inspect container sockets via ss/netstat
        try:
            proc = await asyncio.create_subprocess_exec(
                "docker", "exec", container_name,
                "sh", "-c", "ss -tlpn || netstat -tlpn || cat /proc/net/tcp",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            out, _ = await proc.communicate()
            text = out.decode("utf-8", errors="replace")
            import re
            for match in re.finditer(r":(\d{2,5})\b", text):
                p = int(match.group(1))
                if p not in (22, 5432, 6379) and 1024 <= p <= 65535:
                    ports.add(p)
        except Exception:
            pass

        # 2. Also check common dev ports locally if connected
        for common_port in (3001, 5173, 3000, 8000, 8080):
            try:
                reader, writer = await asyncio.wait_for(
                    asyncio.open_connection("127.0.0.1", common_port),
                    timeout=0.08,
                )
                writer.close()
                await writer.wait_closed()
                ports.add(common_port)
            except Exception:
                pass

        return sorted(list(ports))

    async def get_diff(self, lane_id: str) -> GitDiffInfo:
        cwd = await self.resolve_lane_cwd(lane_id)
        lane = self._lanes.get(lane_id)
        branch_name = lane.branch_name if lane else "main"

        diff_text = ""
        raw_files: list[dict[str, Any]] = []
        files_changed = 0
        insertions = 0
        deletions = 0

        try:
            proc = await asyncio.create_subprocess_exec(
                "git", "diff", "HEAD",
                cwd=cwd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            out, _ = await proc.communicate()
            diff_text = out.decode("utf-8", errors="replace")

            # Numstat breakdown
            stat_proc = await asyncio.create_subprocess_exec(
                "git", "diff", "--numstat", "HEAD",
                cwd=cwd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            s_out, _ = await stat_proc.communicate()
            stat_lines = s_out.decode("utf-8", errors="replace").strip().splitlines()
            for line in stat_lines:
                parts = line.split("\t")
                if len(parts) >= 3:
                    ins = int(parts[0]) if parts[0].isdigit() else 0
                    dels = int(parts[1]) if parts[1].isdigit() else 0
                    filepath = parts[2]
                    insertions += ins
                    deletions += dels
                    files_changed += 1
                    raw_files.append({
                        "file": filepath,
                        "insertions": ins,
                        "deletions": dels,
                        "status": "modified",
                    })
        except Exception:
            pass

        return GitDiffInfo(
            lane_id=lane_id,
            branch_name=branch_name,
            files_changed=max(files_changed, diff_text.count("diff --git")),
            insertions=insertions or (diff_text.count("\n+") - diff_text.count("\n+++")),
            deletions=deletions or (diff_text.count("\n-") - diff_text.count("\n---")),
            diff_text=diff_text,
            raw_files=raw_files,
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
        """Merge lane branch into target branch."""
        host_id, lane_path, _ = await self._resolve_lane_and_host(lane_id)
        host_dir = os.path.join(self.base_workdir, host_id)
        repo_dir = os.path.join(host_dir, "repo")

        lane = self._lanes.get(lane_id)
        branch_name = lane.branch_name if lane else "main"

        # 1. Commit unstaged changes in lane
        await (await asyncio.create_subprocess_exec("git", "add", ".", cwd=lane_path, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)).communicate()
        commit_proc = await asyncio.create_subprocess_exec(
            "git", "-c", "user.name=Congruence", "-c", "user.email=agent@congruence.dev",
            "commit", "-m", f"Auto-save changes from {branch_name}",
            cwd=lane_path, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        await commit_proc.communicate()

        # 2. Merge into repo_dir if different branch
        if branch_name != target_branch and os.path.exists(repo_dir):
            await (await asyncio.create_subprocess_exec("git", "checkout", target_branch, cwd=repo_dir, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)).communicate()
            merge_proc = await asyncio.create_subprocess_exec(
                "git", "-c", "user.name=Congruence", "-c", "user.email=agent@congruence.dev",
                "merge", branch_name, "--no-edit",
                cwd=repo_dir, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
            )
            out, err = await merge_proc.communicate()
            return {
                "success": merge_proc.returncode == 0,
                "lane_id": lane_id,
                "source_branch": branch_name,
                "target_branch": target_branch,
                "output": (out or err).decode("utf-8", errors="replace"),
            }

        return {
            "success": True,
            "lane_id": lane_id,
            "source_branch": branch_name,
            "target_branch": target_branch,
            "output": f"Changes in {branch_name} recorded.",
        }

    async def capture_lane_screenshot(self, lane_id: str, port: int = 3001) -> dict[str, Any]:
        """Capture DOM snapshot / screenshot of the running preview app."""
        import httpx
        url = f"http://127.0.0.1:{port}/"
        html = ""
        status_code = 0
        try:
            async with httpx.AsyncClient(timeout=4.0) as client:
                res = await client.get(url)
                status_code = res.status_code
                html = res.text[:2000]
        except Exception as e:
            html = f"Failed to fetch preview DOM: {e}"

        return {
            "lane_id": lane_id,
            "port": port,
            "url": url,
            "status": "ready" if status_code == 200 else "offline",
            "status_code": status_code,
            "html_preview": html,
            "timestamp": datetime.now(UTC).isoformat(),
        }
