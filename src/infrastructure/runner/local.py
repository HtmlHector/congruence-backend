"""Local Process Runner implementing HostBackend for development and local testing."""

from __future__ import annotations

import asyncio
import errno
import os
import pty
import struct
import tempfile
import termios
from pathlib import Path
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

    async def create_host(
        self, project_id: str, size: str = "standard", repo_url: str | None = None
    ) -> HostInfo:
        import nanoid
        host_id = f"host_{nanoid.generate(size=12)}"
        host_dir = os.path.join(self.base_workdir, host_id)
        os.makedirs(host_dir, exist_ok=True)
        
        # Initialize bare repository or repo workspace
        repo_dir = os.path.join(host_dir, "repo")
        os.makedirs(repo_dir, exist_ok=True)
        
        cloned_successfully = False
        if repo_url and repo_url.strip():
            url = repo_url.strip()
            if not url.startswith("http://") and not url.startswith("https://") and not url.startswith("git@"):
                url = f"https://github.com/{url}.git"
            
            try:
                proc = await asyncio.create_subprocess_exec(
                    "git", "clone", url, repo_dir,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                await proc.communicate()
                if proc.returncode == 0:
                    cloned_successfully = True
            except Exception:
                pass
        
        if not cloned_successfully and not os.path.exists(os.path.join(repo_dir, ".git")):
            import shutil
            local_candidates = []
            if project_id:
                local_candidates.extend([
                    os.path.expanduser(f"~/.congruence/projects/{project_id}"),
                    os.path.expanduser(f"~/.congruence/projects/{project_id.replace('proj-', '')}"),
                ])
                if "meridian" in project_id.lower():
                    local_candidates.append(os.path.expanduser("~/.congruence/projects/meridian-api"))
            if repo_url and "meridian" in repo_url.lower():
                local_candidates.append(os.path.expanduser("~/.congruence/projects/meridian-api"))

            for lp in local_candidates:
                if os.path.isdir(lp) and os.listdir(lp):
                    try:
                        shutil.copytree(lp, repo_dir, dirs_exist_ok=True)
                        break
                    except Exception:
                        pass

            if not os.path.exists(os.path.join(repo_dir, ".git")):
                proc = await asyncio.create_subprocess_exec(
                    "git", "init", "-b", "main", repo_dir,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                await proc.communicate()

        # Ensure at least one commit exists in the repo so worktrees can branch off HEAD
        try:
            commit_check = await asyncio.create_subprocess_exec(
                "git", "rev-parse", "HEAD",
                cwd=repo_dir,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await commit_check.communicate()
            if commit_check.returncode != 0:
                readme_path = os.path.join(repo_dir, "README.md")
                if not os.path.exists(readme_path):
                    with open(readme_path, "w", encoding="utf-8") as f:
                        f.write(f"# {project_id}\n\nInitialized in Congruence workspace.\n")
                
                await asyncio.create_subprocess_exec("git", "config", "user.name", "Congruence Host", cwd=repo_dir)
                await asyncio.create_subprocess_exec("git", "config", "user.email", "host@congruence.dev", cwd=repo_dir)
                p_add = await asyncio.create_subprocess_exec("git", "add", ".", cwd=repo_dir)
                await p_add.communicate()
                p_comm = await asyncio.create_subprocess_exec("git", "commit", "-m", "Initial workspace commit", cwd=repo_dir)
                await p_comm.communicate()
        except Exception:
            pass

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

    async def resolve_lane_cwd(self, lane_id: str) -> str:
        """Resolve the active filesystem worktree directory for a lane ID."""
        if lane_id in self._lanes:
            path = self._lanes[lane_id].worktree_path
            if os.path.exists(path):
                return path

        # Query DB for lane and host repository
        try:
            from sqlalchemy import select
            from src.contexts.congruence.models import Host, Lane
            from src.infrastructure.database.session import AsyncSessionFactory

            async with AsyncSessionFactory() as session:
                lane = (await session.execute(select(Lane).where(Lane.id == lane_id))).scalar_one_or_none()
                if lane:
                    # If lane's worktree_path doesn't exist yet or is empty, ensure worktree is created
                    if not os.path.exists(lane.worktree_path) or not os.listdir(lane.worktree_path):
                        host = (await session.execute(select(Host).where(Host.id == lane.host_id))).scalar_one_or_none()
                        if host:
                            repo_dir = os.path.join(self.base_workdir, host.id, "repo")
                            if os.path.exists(os.path.join(repo_dir, ".git")):
                                if lane.is_pair_lane:
                                    lane.worktree_path = repo_dir
                                else:
                                    # Create worktree
                                    os.makedirs(os.path.dirname(lane.worktree_path), exist_ok=True)
                                    proc = await asyncio.create_subprocess_exec(
                                        "git", "worktree", "add", "-b", lane.branch_name, lane.worktree_path,
                                        cwd=repo_dir,
                                        stdout=asyncio.subprocess.PIPE,
                                        stderr=asyncio.subprocess.PIPE,
                                    )
                                    await proc.communicate()
                                    if proc.returncode != 0:
                                        proc2 = await asyncio.create_subprocess_exec(
                                            "git", "worktree", "add", lane.worktree_path, lane.branch_name,
                                            cwd=repo_dir,
                                            stdout=asyncio.subprocess.PIPE,
                                            stderr=asyncio.subprocess.PIPE,
                                        )
                                        await proc2.communicate()
                                await session.commit()

                    if os.path.exists(lane.worktree_path):
                        return lane.worktree_path

                    # If worktree_path still doesn't exist, fall back to repo dir
                    host = (await session.execute(select(Host).where(Host.id == lane.host_id))).scalar_one_or_none()
                    if host:
                        repo_dir = os.path.join(self.base_workdir, host.id, "repo")
                        if os.path.exists(repo_dir):
                            return repo_dir

                # If lane_id is 'main' or 'pair-lane' or slug
                lane_by_slug = (await session.execute(select(Lane).where((Lane.slug == lane_id) | (Lane.name == lane_id)))).scalars().first()
                if lane_by_slug and os.path.exists(lane_by_slug.worktree_path):
                    return lane_by_slug.worktree_path

                # Fallback: check most recently active host repo
                host = (await session.execute(select(Host).order_by(Host.created_at.desc()))).scalars().first()
                if host:
                    repo_dir = os.path.join(self.base_workdir, host.id, "repo")
                    if os.path.exists(repo_dir):
                        return repo_dir
        except Exception:
            pass

        return self.base_workdir

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
            # Check if repo_dir has git repository
            if os.path.exists(os.path.join(repo_dir, ".git")):
                # Create actual git worktree
                try:
                    proc = await asyncio.create_subprocess_exec(
                        "git", "worktree", "add", "-b", branch_name, worktree_path,
                        cwd=repo_dir,
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE,
                    )
                    await proc.communicate()
                    if proc.returncode != 0:
                        proc2 = await asyncio.create_subprocess_exec(
                            "git", "worktree", "add", worktree_path, branch_name,
                            cwd=repo_dir,
                            stdout=asyncio.subprocess.PIPE,
                            stderr=asyncio.subprocess.PIPE,
                        )
                        await proc2.communicate()
                except Exception:
                    os.makedirs(lane_path, exist_ok=True)
            else:
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

        master_fd, slave_fd = pty.openpty()
        
        # Set initial terminal size
        winsize = struct.pack("HHHH", rows, cols, 0, 0)
        import fcntl
        fcntl.ioctl(master_fd, termios.TIOCSWINSZ, winsize)

        # Generate sandbox jail configuration to lock shell traversal to worktree
        sandbox_dir = Path.home() / ".congruence" / "sandboxes" / lane_id
        sandbox_dir.mkdir(parents=True, exist_ok=True)
        
        jail_script = f"""# Congruence Worktree Sandbox Jail
export CONGRUENCE_WORKTREE_ROOT="{cwd}"
export WORKTREE_ROOT="{cwd}"
export HOME="{cwd}"
export PWD="{cwd}"

# Guard directory changes to prevent escaping the worktree jail
cd() {{
  local target="${{1:-$CONGRUENCE_WORKTREE_ROOT}}"
  if [ "$target" = "~" ] || [ -z "$1" ]; then
    target="$CONGRUENCE_WORKTREE_ROOT"
  fi
  local dest=$(python3 -c "import os, sys; print(os.path.realpath(os.path.expanduser(sys.argv[1])))" "$target" 2>/dev/null || echo "$target")
  
  case "$dest" in
    "$CONGRUENCE_WORKTREE_ROOT"*)
      builtin cd "$dest"
      ;;
    *)
      printf "\\033[1;33m🔒 [Congruence Sandbox]\\033[0m Directory traversal blocked: '%s' is outside this worktree.\\n" "$dest"
      printf "\\033[0;90m   Lane worktree: %s\\033[0m\\n" "$CONGRUENCE_WORKTREE_ROOT"
      builtin cd "$CONGRUENCE_WORKTREE_ROOT"
      ;;
  esac
}}

pushd() {{
  if [ -z "$1" ]; then
    builtin pushd
    return
  fi
  local dest=$(python3 -c "import os, sys; print(os.path.realpath(os.path.expanduser(sys.argv[1])))" "$1" 2>/dev/null || echo "$1")
  case "$dest" in
    "$CONGRUENCE_WORKTREE_ROOT"*)
      builtin pushd "$dest"
      ;;
    *)
      printf "\\033[1;33m🔒 [Congruence Sandbox]\\033[0m Directory traversal blocked: '%s' is outside this worktree.\\n" "$dest"
      ;;
  esac
}}

# Clean prompt with worktree badge
if [ -n "$ZSH_VERSION" ]; then
  PROMPT="%F{{cyan}}➜ %F{{green}}%~%F{{yellow}} [worktree] %F{{reset}}%% "
else
  PS1="\\[\\033[01;32m\\]\\w [worktree] \\$ \\[\\033[00m\\]"
fi
"""
        (sandbox_dir / ".zshrc").write_text(jail_script)
        (sandbox_dir / ".bashrc").write_text(jail_script)
        (sandbox_dir / ".profile").write_text(jail_script)

        if isinstance(command, list):
            cmd_args = command
        elif command in ("/bin/bash", "bash"):
            cmd_args = ["/bin/bash", "--rcfile", str(sandbox_dir / ".bashrc"), "-i"]
        elif command in ("/bin/sh", "/bin/zsh", "sh", "zsh"):
            cmd_args = [command, "-i"]
        else:
            cmd_args = ["/bin/sh", "-c", command]

        merged_env = os.environ.copy()
        if env:
            merged_env.update(env)
        merged_env["HOME"] = cwd
        merged_env["CONGRUENCE_WORKTREE_ROOT"] = cwd
        merged_env["ZDOTDIR"] = str(sandbox_dir)
        merged_env["BASH_ENV"] = str(sandbox_dir / ".bashrc")
        merged_env["ENV"] = str(sandbox_dir / ".profile")
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
        cwd = await self.resolve_lane_cwd(lane_id)
        
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

        if not os.path.exists(cwd):
            return GitDiffInfo(
                lane_id=lane_id,
                branch_name=branch_name,
                files_changed=0,
                insertions=0,
                deletions=0,
                diff_text="",
            )

        proc = await asyncio.create_subprocess_exec(
            "git", "diff", "--stat",
            cwd=cwd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        diff_text = stdout.decode("utf-8", errors="replace")
        
        return GitDiffInfo(
            lane_id=lane_id,
            branch_name=branch_name,
            files_changed=1 if diff_text else 0,
            insertions=1 if diff_text else 0,
            deletions=0,
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
        """Read a file as binary and return (base64_string, file_size_bytes).
        
        Used for image and other binary files that cannot be decoded as UTF-8 text.
        """
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
        """Merge a lane's branch into target branch with automatic commit & conflict detection."""
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

        # Ensure WIP changes in lane are committed
        proc_status = await asyncio.create_subprocess_exec(
            "git", "status", "--porcelain",
            cwd=cwd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        status_out, _ = await proc_status.communicate()
        if status_out.strip():
            await asyncio.create_subprocess_exec(
                "git", "add", ".",
                cwd=cwd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await asyncio.create_subprocess_exec(
                "git", "-c", "user.name=Congruence", "-c", "user.email=bot@congruence.dev",
                "commit", "-m", f"Auto-commit before merge from {branch_name}",
                cwd=cwd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

        # Merge branch into repo/main
        host_dir = os.path.dirname(cwd)
        repo_root = os.path.join(host_dir, "repo") if os.path.exists(os.path.join(host_dir, "repo")) else cwd

        proc_merge = await asyncio.create_subprocess_exec(
            "git", "merge", branch_name, "--no-edit",
            cwd=repo_root,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        merge_out, merge_err = await proc_merge.communicate()

        if proc_merge.returncode == 0:
            return {
                "success": True,
                "status": "merged",
                "merged_branch": branch_name,
                "target_branch": target_branch,
                "output": merge_out.decode("utf-8", errors="replace"),
            }
        else:
            return {
                "success": False,
                "status": "conflict",
                "merged_branch": branch_name,
                "target_branch": target_branch,
                "error": merge_err.decode("utf-8", errors="replace"),
                "output": merge_out.decode("utf-8", errors="replace"),
            }
