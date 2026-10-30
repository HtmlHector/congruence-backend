# Congruence — Product & Technical Specification

**Status:** Draft v1 (interview-derived)
**Date:** 2026-10-23
**Owner:** Product/Platform
**Target repo:** `parabox-backend-starter` (FastAPI + PostgreSQL/RLS + Redis + RabbitMQ + S3 + Socket.IO + Clerk + Stripe)
**Build posture:** Production-bound paid launch, not an MVP spike. Backend first; web client is a separate consumer of this API.

---

## 1. Executive summary

Congruence is a **worktree-first web ADE (agent development environment)**. A user connects a repository, gets a persistent cloud Linux devbox for that project, and fans out work into git worktrees. Every worktree is a first-class task object with its own branch, its own set of terminals, and its own agents (native Claude Code / Codex CLIs running in a real PTY). Everything is reachable from a browser, from a phone browser, and over a REST + WebSocket API — no local install, no laptop requirement.

The wedge against boxes.dev:

| | boxes.dev | Orca | Congruence |
|---|---|---|---|
| Unit of compute | One machine **per agent** | Local machine, worktrees | One persistent machine **per project**, many worktrees |
| Agent model | Native CLI | Native CLI | Native CLI, per-terminal credentials |
| Access | Desktop/mobile/CLI + Slack/Linear | Desktop app | Browser-first + REST/WS API |
| Isolation boundary | VM per agent | OS processes | VM per project, per-terminal tmux + env |
| Cost story | Sleeping boxes free | Your laptop | Project box pauses on idle, meters awake seconds |

Congruence is not a model vendor and not a harness vendor. It runs the real `claude` and `codex` binaries against the user's own subscription or API key.

### Why one box per project instead of one per agent

- A worktree is a directory inside a warm machine that already has dependencies installed, caches primed, and dev services running. Worktree creation is sub-second instead of a multi-minute clone + install.
- Package managers, Docker caches, Playwright browsers, and language toolchains are paid for once per project, not once per agent.
- It matches how developers actually think: "my project" then "the tasks I'm running in it."
- The accepted trade-off (explicitly chosen): agents on the same box can see each other's files and compete for CPU. Mitigations in §9.4 and §22.

---

## 2. Product definition

### 2.1 One-liner

> Give every project a cloud computer. Fan out work into worktrees. Run as many terminals and coding agents as you need in each, from any browser.

### 2.2 Target users

1. **The solo senior dev with a Claude Max / ChatGPT Pro subscription** — already pays for agents, wants to run 3–6 in parallel without branch juggling or laptop heat.
2. **The 2–15 person product team** — wants shared visibility into what agents are doing, a place to review diffs, and central spend control.
3. **The API consumer** — wants to kick off worktree work from CI, a script, or an internal tool.

### 2.3 Jobs to be done

- "I have five things to ship; give me five worktrees and let agents grind while I review."
- "My laptop died / I'm on my iPad; my agents should keep going."
- "Show me what the agent actually did, including the shell commands, not a summary."
- "Our team runs 40 agents a day; I need one bill, one audit trail, and a way to stop a runaway."

---

## 3. Goals and success metrics

### 3.1 Product goals (launch)

| Goal | Metric | Target |
|---|---|---|
| Onboarding works without support | % of new users who create a project + first worktree unattended | ≥ 70% |
| The core loop is fast | p95 time from "create worktree" to usable agent terminal | ≤ 25 s warm box, ≤ 90 s cold |
| Terminal feels local | p95 input→echo round trip, same region | ≤ 150 ms |
| Agents survive disconnects | % of terminal reattaches with no lost output (last 2 MB) | ≥ 99.5% |
| Predictable cost | % of boxes paused within 20 min of last activity | ≥ 95% |
| Trust | Agent credentials never appear in stored transcripts | 0 incidents |
| Monetization | Trial→paid conversion in first 30 days | ≥ 8% |

### 3.2 Engineering goals

- Multi-tenant isolation is enforced in the database (RLS), not only in application code.
- Every external (provider) call is idempotent and reconcilable; no orphaned paid compute.
- Zero data loss on control-plane restart: terminal output is streamed to durable storage.
- The provider is swappable (Fly primary, E2B alternate, local Docker for development) behind one interface.

---

## 4. Scope

### 4.1 In scope for launch

- GitHub App + GitLab OAuth repository connection, one repo per project, repo root only.
- One persistent devbox per project, created from a fixed base image, paused on idle, resumed on demand.
- Project setup: clone, run a user-supplied setup command once, stream logs, retry.
- Worktrees: create from a base branch, task metadata, PR link, merge detection, delete, soft-delete.
- Terminals: many per worktree (tmux-backed), reattachable, persisted transcripts, resize, multiple concurrent viewers.
- Agents: Claude Code and Codex, launched in a terminal, per-terminal credential injection.
- Agent login: user runs `claude login` / `codex login` inside a worktree terminal; credential captured from the CLI credential file, encrypted, stored per user, injected into future terminals of that user.
- Managed encrypted env store at workspace and project scope, materialized per worktree.
- Preview URLs per worktree via wildcard subdomain and a reverse tunnel.
- Explicit non-goals below.

### 4.2 Explicit non-goals for launch

| Deferred | Why |
|---|---|
| Slack and Linear triggers | Each is its own auth, dedupe, and reply-routing product; web + API first. |
| Scheduled / cron agent runs | Needs a scheduler product surface and timezone semantics. |
| Native iOS/Android apps | Responsive web only for launch. |
| In-app PR review and merge | Worktrees link to PRs; review stays in the git host. |
| Browser / computer-use agents | Out of scope for the PTY-first model. |
| Self-hosted / BYOC deployment | Managed single-region launch. |
| Multi-repo projects | One repository per project. |
| Agent-to-agent orchestration / DAGs | Worktrees and terminals only; no task graph. |
| Monorepo subdirectory project roots | Repo root only in v1; the schema carries `root_path` for later. |
| Warm box pools | Pause/resume + async UX first; pool later if p95 demands it. |
| Per-worktree containers / devcontainers | VM is the isolation boundary; see §9.4. |

---

## 5. Core user journeys

### J1 — First project

1. Sign in with Clerk (Google / email). JIT user + default workspace provisioning already exists in `contexts/identity` and `contexts/workspace`.
2. Install the Congruence GitHub App for the workspace (or connect GitLab), granting access to selected repositories.
3. `POST /projects` with repo, default branch, name, setup command.
4. Backend creates the project row and a devbox (`provisioning`), enqueues `devbox.provision`.
5. Box boots from the pinned image, box-agent dials home, backend clones the repo, runs the setup command, streams logs to the UI.
6. On success: `project.ready`; UI offers "New worktree".

### J2 — Create a worktree and start an agent

1. User clicks **New worktree**, enters title (and optional initial prompt), picks base branch, picks agent (Claude Code / Codex / shell).
2. If the box is paused, the API returns `202` with `box_status: waking`; UI renders a skeleton ("waking devbox").
3. Worker resumes the box, allocates a worktree slot + port block, runs `git fetch` + `git worktree add -b congruence/<slug> <path> origin/<base>`.
4. Env is materialized (managed env + per-worktree variables), git credential helper installed for the box user.
5. A terminal of `kind=agent` is created for the requesting user, with that user's credential injected via a per-terminal config dir.
6. Worktree status `ready`; realtime event `worktree:ready`; the UI attaches the terminal over WebSocket.

### J3 — Watch an agent from a phone, then disconnect

1. Terminal output streams to the browser and is teed into durable storage.
2. User closes the tab. tmux keeps the process alive. Gateway detaches.
3. User reopens on mobile, `POST /terminals/{id}/attach` returns a 60 s ticket, WS connects, gateway replays the last ≤ 2 MB of scrollback, then live frames.
4. If the box paused meanwhile, the attach triggers resume and waits; the WS sends `status` frames while waking.

### J4 — Connect an agent subscription

1. On a worktree, the "Claude" terminal is created with `kind=login` and recording disabled.
2. The UI types/asks the user to run `claude login`, which prints a URL + code.
3. User completes the flow in a new tab. The CLI writes the credential file.
4. box-agent detects the file, reads it, and posts it to the control plane with the terminal's one-time capture token.
5. Backend encrypts and stores it against `(workspace, user, provider)`, marks it `active`, emits `credential:captured`, and offers a one-click "test agent".
6. Subsequent agent terminals for that user inject the credential. Other users on the same shared box never see it.

### J5 — Share a running app

1. Agent starts a dev server on an allocated port (e.g. `3100`).
2. box-agent reports the listening port. Backend registers a preview route.
3. UI shows `https://<project>-<worktree>-3100.preview.congruence.dev`.
4. Teammates in the workspace open it; the preview edge checks the workspace cookie and proxies through the box tunnel.

### J6 — Ship and clean up

1. Agent pushes (`git push` via credential helper → short-lived token) and opens a PR; backend records `pr_url`, status `needs_review`.
2. GitHub webhook → PR merged → worktree status `merged`.
3. User (or the UI prompt) deletes the worktree: tmux sessions killed, `git worktree remove`, directory deleted, row soft-deleted.
4. 30 days later, purge job removes transcripts, preview routes, and branch refs; the box may already have been rebuilt.

---

## 6. Domain model

### 6.1 Aggregates and entities

All aggregates carry `workspace_id` and are protected by both RLS and repository filtering, following the existing starter pattern.

| Aggregate | Key fields | Notes |
|---|---|---|
| **Project** (extends existing `projects`) | `repository_id`, `repo_provider`, `default_branch`, `root_path` (""), `setup_command`, `setup_status`, `setup_log_ref`, `base_image_ref`, `box_id`, `created_by` | Replaces the sample CRUD semantics. Statuses: `draft`, `provisioning`, `ready`, `setup_failed`, `archived`. |
| **Devbox** | `project_id`, `provider`, `provider_box_id`, `region`, `status`, `image_ref`, `cpu`, `memory_mb`, `disk_gb`, `last_activity_at`, `awake_seconds_total`, `last_resumed_at`, `paused_at`, `error_code` | One per project. Statuses in §6.2. |
| **Worktree** | `project_id`, `devbox_id`, `title`, `initial_prompt`, `branch`, `base_branch`, `path`, `status`, `pr_number`, `pr_url`, `head_sha`, `merged_at`, `deleted_at`, `created_by` | Task metadata is first-class; terminal remains the runtime source of truth. |
| **Terminal** | `worktree_id`, `devbox_id`, `tmux_session`, `kind` (`shell`/`agent`/`login`), `agent` (`claude`/`codex`/null), `status`, `title`, `created_by`, `credential_id`, `recording_enabled`, `last_attached_at`, `scrollback_bytes` | `kind=login` is always `recording_enabled=false`. |
| **TerminalChunk** | `terminal_id`, `seq`, `content` (compressed bytea), `created_at` | Append-only, partitioned monthly, retention §15.4. |
| **AgentCredential** | `user_id`, `workspace_id`, `provider` (`anthropic`/`openai`), `kind` (`api_key`/`oauth`), `secret_ciphertext`, `expires_at`, `status` (`active`/`needs_reconnect`/`revoked`), `last_verified_at`, `label` | **One active per (user, provider)**, private to the user, RLS includes `user_id`. |
| **EnvVar** | `scope` (`workspace`/`project`), `project_id?`, `key`, `value_ciphertext`, `is_secret`, `updated_by` | Materialized per worktree; never rendered back in plaintext through the API. |
| **PreviewRoute** | `worktree_id`, `port`, `slug`, `hostname`, `status` (`pending`/`live`/`stale`), `last_seen_at` | Registered from box-agent port reports. |
| **UsageRecord** | `project_id`, `devbox_id`, `kind` (`box_awake_seconds`), `quantity`, `period_start`, `period_end`, `reported_to_stripe_at` | Double-entry-ish: written on pause and on 60 s heartbeat. |
| **PersonalAccessToken** | `user_id`, `workspace_id`, `name`, `token_hash`, `scopes`, `last_used_at`, `expires_at` | For the public REST API. |
| **BoxEvent** | `devbox_id`, `type`, `payload`, `occurred_at` | Append-only lifecycle log; feeds the reconciler and support tooling. |

### 6.2 State machines

**Devbox**

```
provisioning ──► awake ──► pausing ──► paused ──► waking ──► awake
     │             │                     │
     └──► error ◄──┴─────────────────────┘
                     destroyed (terminal)
```

- `provisioning`: provider create accepted; waiting for box-agent handshake (timeout 180 s → `error`).
- `awake`: box-agent connected, heartbeat ≤ 30 s old.
- `pausing`: pause API accepted; box still reachable (drain: stop accepting new terminals).
- `paused`: provider confirms; filesystem retained; no compute billed.
- `waking`: resume requested; attach requests queue.
- `error`: last provider call failed after retries; reconciler owns retry with backoff.
- `destroyed`: terminal state; provider resource deleted; row retained 30 days for audit.

**Worktree**

```
creating ──► ready ──► working ──► needs_review ──► merged
   │           │           │              │
   └─► failed  └───────────┴──────────────┴──► deleted (soft)
```

- `creating`: git worktree and env materialization in flight (timeout 120 s → `failed`).
- `ready`: no terminal has produced output in the last 60 s.
- `working`: any attached or running agent terminal produced output in the last 60 s (derived, not user-set).
- `needs_review`: user or agent marker; PR open.
- `merged`: set by webhook.
- `failed` / `deleted`: terminal-ish.

**Terminal**

```
starting ──► open ──► detached ──► open (reattach)
   │           │
   └─► lost ◄──┴──► closed
```

- `lost`: box cold-booted and tmux lost the session; row keeps scrollback, offers "restart".
- `closed`: tmux session exited or user killed it.

### 6.3 Invariants

1. A project has at most one live devbox (`status NOT IN ('destroyed','error')`).
2. A worktree belongs to exactly one project and one devbox; `path` is unique per project.
3. A terminal belongs to one worktree; `tmux_session` is unique per devbox.
4. A terminal's `credential_id` must belong to the terminal's `created_by` user.
5. `kind=login` implies `recording_enabled=false`, enforced by a DB check constraint.
6. Deleting a worktree kills its terminals before removing the directory.
7. `box_awake_seconds` for a devbox is monotonically increasing and never double-counted across a pause/resume boundary.
8. No secret is ever returned by the API in plaintext after creation (write-only semantics).

---

## 7. System architecture

### 7.1 Components

```
                        ┌────────────────────────────────────────────┐
  Browser (web app) ────┤  FastAPI API (existing: src/adapters/api)   │
   REST + Socket.IO     │  auth, tenancy, projects, worktrees, billing│
                        └───────┬───────────────┬────────────────────┘
                                │               │
             ┌──────────────────▼──┐      ┌─────▼──────────────────┐
             │ Terminal Gateway    │      │ Preview Edge           │
             │ (separate process)  │      │ wildcard TLS + auth    │
             │ WS ⇄ box-agent      │      │ HTTP/WS → box tunnel   │
             └──────────┬──────────┘      └─────┬──────────────────┘
                        │ Redis Streams         │
                        │ (box:{id}:in/out)     │
                        └──────────┬────────────┘
                                   │
             ┌─────────────────────▼──────────────────────┐
             │  box-agent (in devbox, dials out over WSS) │
             │  tmux, PTY, exec, git, ports, credential   │
             └─────────────────────┬──────────────────────┘
                                   │
                        ┌──────────▼─────────┐
                        │ Fly Sprites (primary) │
                        │ E2B (alternate)       │
                        │ local Docker (dev)    │
                        └────────────────────┘

  Workers (existing src/bootstrap/workers.py):
    devbox.reconciler | setup.runner | worktree.runner | usage.meter
    transcript.writer | pr.poller | purge.runner

  Existing infra: Postgres (RLS), Redis (cache/pubsub), RabbitMQ (events), S3 (logs/artifacts)
```

### 7.2 Why a box-agent instead of provider-native exec

- **Provider portability.** Fly Sprites, E2B, and local Docker expose different exec/PTY APIs. One agent implementation makes the provider layer thin (create/pause/resume/destroy/status + a bootstrap command).
- **One transport for everything.** PTY streaming, port reporting, credential capture, and git credential minting share a single authenticated outbound WSS connection. No inbound firewall holes, no SSH key distribution.
- **Local development parity.** The same agent runs inside a Docker container locally, so the full terminal/recording/tunnel path is exercised in `make dev`.
- **Cost.** No per-box sidecar container, no provider-specific SDK in the request path.

### 7.3 Terminal data path

```
tmux pane ──► box-agent (PTY reader, 16 KB chunks)
          ──► WSS frame (msgpack {terminal_id, seq, bytes})
          ──► gateway; publishes to Redis Stream box:{devbox_id}:out
          ──► each subscribed gateway instance fans out to its browser sockets
          ──► transcript writer consumes the same stream → Postgres chunks
```

- Gateway instances subscribe to `box:{id}:out` for boxes they have browser clients for. Multiple gateways can serve the same box; the stream is the fan-out.
- Browser input goes to `box:{id}:in` with `terminal_id`; the gateway holding that box-agent connection forwards it. Input echo/ack is `seq`-based.
- Backpressure: if a browser socket's buffer exceeds 8 MB, drop the socket (client reattaches with scrollback) rather than stalling the box.
- Chunk size 16 KB, coalesced to 50 ms flush windows to reduce frame overhead on chatty TUIs.

### 7.4 Protocol (browser ⇄ gateway)

WS endpoint: `GET /api/v1/terminals/{terminal_id}/stream?ticket=<signed>` (upgrade).

Server → client (JSON control, binary for data):

```json
{"type":"attached","terminal_id":"...","seq":8123,"cols":120,"rows":32,"box_status":"awake"}
{"type":"output","seq":8124,"bytes":16384}          // followed by a binary frame
{"type":"status","box_status":"waking"}
{"type":"exit","code":0}
{"type":"error","code":"BOX_UNAVAILABLE","message":"..."}
```

Client → server:

```json
{"type":"input","seq":8124,"bytes":12}
{"type":"resize","cols":140,"rows":40}
{"type":"detach"}
{"type":"ping"}
```

Rules:
- Attach requires a single-use ticket (60 s TTL, bound to `terminal_id`, `user_id`, `workspace_id`).
- Replay: last ≤ 2 MB from Redis (fast path) then Postgres chunks if the Redis buffer was evicted.
- Binary frames are length-prefixed and never JSON-encoded (base64 doubles bandwidth).
- `seq` is monotonic per terminal and assigned by the gateway that first sees the chunk; the writer uses it as the dedupe key.

### 7.5 Request path for REST

All new routes follow existing conventions:

- `Depends(get_active_tenant)` for workspace context, `Depends(get_db)` for a fail-closed RLS transaction.
- Application service per context, audit entry per mutation, Socket.IO event per state change, RabbitMQ routing key for cross-context work.
- No provider calls inside the request path except reads; creates return `202` + resource id.

---

## 8. Provider layer

### 8.1 Interface

`src/infrastructure/devbox/provider.py`

```python
class DevboxProvider(Protocol):
    name: Literal["fly", "e2b", "local"]
    async def create(self, spec: BoxSpec) -> ProviderBox: ...
    async def resume(self, box_id: str) -> ProviderBox: ...
    async def pause(self, box_id: str) -> None: ...
    async def destroy(self, box_id: str) -> None: ...
    async def status(self, box_id: str) -> ProviderStatus: ...
    async def snapshot(self, box_id: str) -> str | None: ...      # optional
    async def restore(self, snapshot_id: str) -> ProviderBox: ...  # optional
```

`BoxSpec`: image ref, region, vcpu, memory_mb, disk_gb, env (non-secret bootstrap only), metadata (project/workspace ids), `name` (provider-safe slug).

Deliberately **not** in the interface: exec, PTY, port forwarding, file read. Those live in box-agent because they must be transport-agnostic.

### 8.2 Provider matrix

| Need | Fly Sprites (primary) | E2B (alternate) | Local Docker (dev/test) |
|---|---|---|---|
| Isolation | Firecracker microVM | Firecracker microVM | container (dev only) |
| Persistent disk | 100 GB ext4, indefinite | pause preserves FS+memory | named volume |
| Idle behavior | pauses ~30 s, warm wake | 5 min default pause | manual |
| Session cap | none | 1 h Hobby / 24 h Pro | none |
| Custom image | yes (Docker image) | yes (Docker image) | yes |
| Port exposure | via box-agent tunnel (we do not rely on provider ingress) | same | same |
| Cost shape | per second while running | per second + memory snapshot | free |

**Decision:** Fly Sprites is the primary backend because the persistent-disk, no-session-cap shape matches a project box that lives for months. E2B is implemented behind the same interface as the capacity/region alternate, not as a second hot path. Local Docker is the default in `development` and CI.

**Risk to retire in M0 spike (blocking):**
1. Does a Sprite survive pause/resume with tmux processes intact, or only the disk (cold wake relaunch)?
2. Can we boot a custom image with box-agent + tmux + Docker CLI in < 45 s?
3. Does the Fly API support programmatic pause and per-second metering events we can reconcile?
4. Do nested workloads (Playwright, Postgres, Docker-in-VM) run?

If (1) or (3) fails, the fallback is Fly Machines + volumes (stateless boot, disk attach), which changes `warm wake` to `cold boot + volume attach` and moves the p95 target.

### 8.3 Failover and provider selection

- `DEVBOX_PROVIDER` setting selects the default; `local` in development/test.
- A devbox records its provider; it never migrates providers mid-life. A project "reset" creates a new box on the current default.
- On repeated `create` failures (3 consecutive, 5 min), the box is marked `error`, the workspace admin is notified, and automatic creation is disabled for 15 min to avoid a retry storm.
- Capacity fallback: if the primary returns quota/region errors, the reconciler may create on the alternate provider for new boxes only, and marks the project `provider_fallback=true`.

---

## 9. Devbox image and runtime

### 9.1 Base image

`ghcr.io/congruence/devbox:<git-sha>`, pinned per release; projects record `base_image_ref` at creation so a bad image rollout cannot break existing boxes (they keep their booted state; new/reset boxes use the pin until migrated).

Contents:

- Ubuntu 24.04, non-root `dev` user (uid 1000) with passwordless sudo, plus root for provider bootstrap.
- `git` + `git-lfs`, `openssh-server` (opt-in), `tmux` ≥ 3.4, `curl`, `jq`, `ripgrep`, `fd`, `build-essential`, `pkg-config`.
- Python 3.12 + `uv`; Node 22 + `corepack`/`pnpm`/`npm`; `bun` optional.
- Docker CLI (daemon present only if the provider supports it; agents get a clear error otherwise).
- `claude` (Claude Code) and `codex` (Codex CLI) preinstalled at pinned versions, updated by a monthly image bump.
- `gh` CLI.
- `box-agent` binary (static, Go or Rust; Python acceptable for v1 but not preferred) + `congruence-git-credential` helper.
- `/etc/congruence/manifest.json`: image version, agent versions, capabilities.

### 9.2 Boot sequence

1. Provider boots the image with `CONGRUENCE_BOX_TOKEN` (short-lived, mTLS-pinned) and `CONGRUENCE_CONTROL_PLANE_URL` in env.
2. `box-agent` starts as a systemd unit, validates the token, opens WSS, sends `hello {box_id, image, agents, capabilities}`.
3. Control plane marks the box `awake`, records boot duration.
4. `box-agent` starts tmux server (if not running), restores known terminal sessions from its local state file, reports them.
5. Heartbeat every 15 s: load, disk free, listening ports, tmux sessions, agent credential status.

### 9.3 Box-agent responsibilities

| Capability | Detail |
|---|---|
| `pty.open` | create tmux session `cng-<terminal_id>` with `-x cols -y rows`, optional command, env, cwd; stream via `tmux pipe-pane`/control mode |
| `pty.input` / `pty.resize` / `pty.close` | write bytes, `tmux resize-window`, `tmux kill-session` |
| `pty.list` | reconcile terminal rows after a mismatch or cold boot |
| `exec` | one-shot command with timeout, stdout/stderr captured (setup, git ops, probes) |
| `git.worktree_add` | run `git fetch` + `git worktree add` with the credential helper configured |
| `ports.report` | `ss -ltnp` diff every 10 s → control plane (preview routes) |
| `credential.capture` | watch the CLI credential paths, POST captured secret with the terminal capture token |
| `credential.status` | preflight probe per provider, cached 10 min |
| `health` | disk, memory, load, tmux, agent versions |
| `tunnel.open` | multiplexed stream for preview HTTP/WS |

`exec` and `pty` are the only ways the control plane touches a box. No SSH requirement, no inbound ports.

### 9.4 Isolation posture (accepted)

- **Boundary:** one VM per project. A worktree is a directory; agents *can* see sibling worktree files and each other's processes.
- **What we do isolate:** per-terminal tmux session, per-terminal agent config dir (`CLAUDE_CONFIG_DIR`, `CODEX_HOME`), per-terminal credential env, per-worktree port block, per-worktree `.env` materialization.
- **What we do not isolate:** filesystem, process table, CPU/memory, network namespace.
- **Explicit residual risk:** a malicious workspace member with shell access on a shared box can read another user's terminal env or `/proc` entries while that process runs. Mitigations: no long-lived credentials on disk (per-terminal dirs are 0700 and deleted on terminal close), audit every terminal open, workspace roles gate who can open terminals at all, and a future workspace policy can force private boxes. This risk is documented in the security review, not hand-waved.
- **Resource fairness:** per-worktree nice-level and a `cgroup` v2 slice per worktree when the provider permits; otherwise an advisory warning at 90% CPU. No hard guarantee in v1 — called out as a known limitation.

---

## 10. Worktree lifecycle and git

### 10.1 Creation

`POST /projects/{id}/worktrees`

```json
{
  "title": "Fix flaky auth test",
  "base_branch": "main",
  "branch": "congruence/fix-flaky-auth-test",   // optional, server-slugified
  "initial_prompt": "The auth test is flaky under parallel load. Find and fix.",
  "agent": "claude"                              // claude | codex | shell | null
}
```

Server steps (all idempotent, keyed by `Idempotency-Key` or `(project, branch)`):

1. Validate: project `ready`, branch not already checked out, plan allows worktree (≤ 50 active per project), worktree cap per box (≤ 20 active).
2. Insert `worktrees` row `creating`, allocate slot index + port block (`30000 + slot*10` … `+9`), record `base_sha` after fetch.
3. Enqueue `worktree.create` on RabbitMQ.
4. Worker: ensure box awake → `box-agent.exec`:
   ```
   git -C /workspaces/<project> fetch origin <base_branch>
   git -C /workspaces/<project> worktree add -b <branch> /workspaces/<project>/wt/<slug> origin/<base_branch>
   ```
5. Materialize env, write `.congruence/worktree.json` (ids, port block, branch), run optional `post_create` hook (project setting, default none).
6. Create terminal if `agent` is set; status `ready`; emit `worktree:ready`.

**Branch naming:** default `congruence/<slug>`; slug from title, ASCII, ≤ 50 chars, `-2` suffix on collision. Never force-push, never rewrite a user's branch.

**Existing branch:** `POST /worktrees/{id}/checkout-existing {branch}` is available *after* creation (fetch + `git worktree add <path> <branch>`) but is not a creation mode in v1.

### 10.2 Git identity and push

- Box-level git config: `user.name`, `user.email` from the workspace member who created the terminal (set per terminal via `GIT_AUTHOR_*` env).
- Credential helper: `git config --local credential.helper '!/usr/local/bin/congruence-git-credential'`. On `get`, the helper asks box-agent for a short-lived token scoped to the project repo and the terminal's creator; on `store`, it discards.
- Tokens: GitHub App installation token (1 h, repo-scoped) or GitLab project access token (1 h). Never written to disk; held in agent memory for the duration of the request.
- `gh` uses the same helper path via `GH_TOKEN` injected per terminal.

### 10.3 PR tracking

- GitHub/GitLab webhooks (`pull_request` / `merge_request`) → match by `head_sha` or branch → update `pr_number`, `pr_url`, status.
- Fallback poller every 5 min for worktrees in `working`/`needs_review` with no webhook in 30 min.
- Merge sets `merged`, emits `worktree:merged`, and surfaces a one-click delete. Never auto-delete (chosen).

### 10.4 Deletion and purge

- `DELETE /worktrees/{id}` → status `deleted`, `deleted_at=now()`, kill tmux sessions, `git worktree remove --force`, delete directory, delete preview routes, detach credentials.
- Local branch retained on the box for 30 days? No: branch moved to a refs archive namespace `refs/congruence/archive/<branch>` so the worktree directory can go without losing commits. Deleted on purge.
- Purge job (daily) hard-deletes rows + transcript chunks + S3 objects at `deleted_at + 30d`. Admin can `?purge=now`.
- Project delete cascades: pause + destroy box, soft-delete all worktrees, stop billing, purge in 30 days.

---

## 11. Terminal subsystem

### 11.1 Model

- Each terminal = one tmux session `cng-<terminal_id>` inside the box, owned by `dev`, started with an explicit cwd and env.
- tmux gives: survive gateway disconnect, survive browser close, multiple viewers, and a server-side scrollback buffer independent of our storage.
- Terminal `kind`:
  - `shell`: plain login shell in the worktree path.
  - `agent`: runs the agent CLI in the worktree path with the user's credential.
  - `login`: runs a shell with recording disabled and a capture token; used for `claude login`/`codex login`.

### 11.2 Limits

- 8 concurrent terminals per worktree (soft; `429 TERMINAL_LIMIT` above).
- 32 concurrent terminals per project; 64 per workspace.
- Scrollback retained: 2 MB in Redis (fast reattach), full history in Postgres (compressed), 30 days.

### 11.3 Attach flow

1. `POST /terminals/{id}/attach` → verify membership + terminal visibility; if box `paused`, enqueue resume and return `202 {"box_status":"waking","retry_after":2}`; if `awake`, return `{"ticket":"...","expires_in":60,"ws_url":"..."}`.
2. Browser opens WS with ticket. Gateway validates ticket (single use, HMAC, 60 s), subscribes to the box stream, sends `attached` + replay.
3. On `detach`/close: terminal `status=detached`; nothing is killed.

### 11.4 Recording

- Gateway assigns `seq` and publishes to Redis Stream `term:{id}:chunks` (`MAXLEN ~ 10000`).
- `transcript.writer` consumer batches every 500 ms or 64 KB → `terminal_chunks` rows, gzip-compressed, partitioned monthly.
- `kind=login` bypasses recording entirely (no Redis stream persistence, no rows). Gateway still forwards live bytes.
- Redaction pass on all recorded content for known token patterns (`sk-ant-…`, `sk-…`, `ghp_…`, `glpat-…`, JWT-shaped strings) before insert; a redaction hit is logged as a security event (never the value).
- Retention: 30 days rolling; S3 export optional per workspace (deferred).

### 11.5 Lost sessions

On box cold boot (provider lost process state), box-agent reconciles: any terminal row without a tmux session becomes `lost`. The UI shows the recorded scrollback plus a **Restart** button that re-creates the tmux session in the worktree path (new `tmux_session` value, same terminal id, seq continues).

---

## 12. Agent credentials and login

### 12.1 Providers

| Agent | Credential source | Config isolation |
|---|---|---|
| Claude Code | `~/.claude/.credentials.json` (OAuth) or `ANTHROPIC_API_KEY` | `CLAUDE_CONFIG_DIR=/home/dev/.congruence/agents/<terminal_id>/claude` |
| Codex | `~/.codex/auth.json` (OAuth) or `OPENAI_API_KEY` | `CODEX_HOME=/home/dev/.congruence/agents/<terminal_id>/codex` |

Per-terminal dirs are `0700`, created at terminal start, deleted at terminal close. This is what makes one credential per user work on a workspace-shared box: the token is materialized only in that terminal's config dir and env.

### 12.2 Login flow (chosen: in-worktree terminal)

1. UI creates `kind=login` terminal for the requesting user and the selected provider. Recording off. A one-time `capture_token` (5 min TTL, bound to terminal + user) is passed to box-agent.
2. The UI shows instructions; the user runs `claude login` / `codex login`.
3. box-agent watches the provider's credential path (inotify + 2 s poll fallback). On change, it reads the file, validates JSON shape, and sends `{capture_token, provider, kind, payload}` over WSS.
4. Backend validates: token unused, terminal is `login`, terminal's `created_by` = token user, terminal not closed. Encrypts with `src/security/encryption.py` (Fernet) and upserts `agent_credentials` for `(user, provider)`.
5. Emits `credential:captured` to `user:{user_id}`; marks the login terminal `closed`; UI offers "Test agent" which runs a 10 s preflight.
6. The captured secret is **never** returned by the API. GET returns `provider`, `kind`, `status`, `expires_at`, `last_verified_at`, `label` only.

### 12.3 API keys

`POST /credentials` with `{provider, kind:"api_key", value}` → encrypted at rest, validated against the provider (cheap models endpoint), stored with `status=active`. Injected as env into matching terminals.

### 12.4 Expiry and refresh

- A probe per provider runs on agent terminal start (cached 10 min) and as a 6-hourly job for users with active boxes: `claude -p "ok"` / `codex exec "ok"` with a 20 s timeout, or a cheaper auth-only check if the CLI exposes one.
- On 401/auth error: credential `status=needs_reconnect`, realtime + email notification, UI badge "Reconnect Claude", and new agent terminals show a reconnect CTA instead of launching with a dead token.
- Refresh tokens are used only if the CLI does so itself when running with the materialized config dir; we never implement provider refresh logic ourselves.
- Revocation: `DELETE /credentials/{provider}` deletes the ciphertext, kills terminals using it, and audits.

### 12.5 Multi-user on a shared box

- Credentials are per user and injected per terminal; there is no box-wide credential env.
- `box-agent` refuses to capture into a terminal whose creator does not match the capture token.
- Audit entries: `credential.capture`, `credential.inject`, `credential.revoke` with `actor_id`, `terminal_id`, never the secret.
- Residual risk documented in §9.4 and §22.

---

## 13. Environment and secrets

- **Scopes:** workspace-level (shared by all projects) and project-level (overrides workspace keys).
- **Storage:** `env_vars` with Fernet-encrypted values; API is write-only for secrets (`is_secret=true` values return `"••••"` after save).
- **Materialization:** on worktree creation, backend pushes the resolved env to box-agent; box-agent writes `/workspaces/<project>/wt/<slug>/.env.congruence` (`0600`) and exports it from a shell snippet sourced by `~/.bashrc` for worktree paths. Also injected into tmux session env so agents and their subprocesses inherit.
- **Per-worktree variables:** `CONGRUENCE_WORKTREE_ID`, `CONGRUENCE_PORT_BASE`, `CONGRUENCE_BRANCH`, and derived `DATABASE_URL` templates if the project opts in (e.g. `postgres://…/app_<slot>`).
- **Change propagation:** editing env queues `env.sync` for awake boxes; new worktrees get it immediately. Running terminals are not mutated (avoid surprising a live agent); a banner offers "restart terminal to apply".
- **Never** commit `.env.congruence`; created `.git/info/exclude` entry plus a warning if the repo tracks `.env*`.
- **Scanning:** on project setup, warn if a tracked file matches common secret patterns.

---

## 14. Previews

- **URL:** `https://<project-slug>-<worktree-slug>-<port>.preview.congruence.dev`.
  - Slugified, ≤ 63 chars per label, collision suffix `-2`.
- **DNS/TLS:** wildcard `*.preview.congruence.dev` cert (ACM/Let's Encrypt DNS-01), one wildcard A record to the preview edge.
- **Tunnel:** box-agent keeps a single multiplexed WSS connection (`/api/v1/box/tunnel`) to the preview edge. Edge maps hostname → `(box_id, port)` via a Redis registry populated by `ports.report` and the backend's `preview_routes` table.
- **Detection:** box-agent runs `ss -ltnp` every 10 s, reports new listening ports bound to `0.0.0.0`/`::`; backend creates a `PreviewRoute` (`pending` → `live` after a successful health probe on `/`).
- **Auth:** edge requires a signed `congruence_preview` cookie minted by the app for logged-in workspace members (`aud=preview:<workspace_id>`, 12 h). Unauthenticated → 302 to app login with `return_to`. Public/shared links are deferred.
- **WebSocket apps:** edge upgrades WS and proxies through the same tunnel; terminal multiplexing frame type `tunnel.data` vs `pty.data` keeps streams distinct.
- **Staleness:** if the port stops listening for 60 s, route → `stale` and the UI shows "server not running" instead of a 502.
- **Abuse:** per-route rate limit (100 rps), request body cap 10 MB, no caching of `Set-Cookie`, and `X-Forwarded-*` rewritten.

---

## 15. Access control, tenancy, and data lifecycle

### 15.1 Roles (existing workspace RBAC)

| Action | Owner | Admin | Member | Viewer |
|---|---|---|---|---|
| View projects/worktrees | ✅ | ✅ | ✅ | ✅ |
| Create worktree / open terminal | ✅ | ✅ | ✅ | ❌ |
| Watch terminal (read-only) | ✅ | ✅ | ✅ | ✅ |
| Kill terminal / delete worktree | ✅ | ✅ | creator or any member* | ❌ |
| Manage env vars | ✅ | ✅ | ❌ | ❌ |
| Connect repo / manage credentials | ✅ | ✅ | own credentials only | ❌ |
| Force-pause box / reset | ✅ | ✅ | ❌ | ❌ |
| Billing / usage | ✅ | ✅ | ❌ | ❌ |

\* Workspace-shared boxes were chosen deliberately; any member may stop a runaway terminal. Audited.

### 15.2 Tenancy

- Every new table has `workspace_id NOT NULL` and an RLS policy:
  `workspace_id = current_setting('app.current_workspace_id', true)::uuid`.
- `agent_credentials` and `personal_access_tokens` add:
  `user_id = current_setting('app.current_user_id', true)::text`.
- `rls.py` gains `app.current_user_id` (set from verified claims in `get_active_tenant`) and a test that an unauthenticated/incorrect-user query returns zero rows.
- Terminal tickets and preview cookies are signed with separate keys (`TERMINAL_TICKET_SECRET`, `PREVIEW_COOKIE_SECRET`) and never reuse the OAuth state key.

### 15.3 Audit

Every mutation records through `AuditService`: `project.create/update/delete`, `devbox.create/pause/resume/reset`, `worktree.create/delete/merge`, `terminal.open/close/watch`, `credential.capture/revoke`, `env.update`, `usage.limit_reached`. Payloads never include secrets, terminal contents, or prompt text (prompt text is product data, not audit data).

### 15.4 Retention

| Data | Retention |
|---|---|
| Terminal transcripts | 30 days rolling after terminal close; deleted with worktree purge |
| Soft-deleted worktrees/projects | 30 days, restorable; admin can purge immediately |
| Box disk | until project delete; destroyed at purge |
| Usage records | 24 months (billing/audit) |
| Audit log | 12 months |
| Box events | 90 days |

Restore: `POST /worktrees/{id}/restore` within the window re-creates the worktree from the archived ref if the box still exists; if the box was rebuilt, restore returns `409 BOX_REBUILT` and offers branch-only restore into a new worktree.

---

## 16. Billing, metering, and limits

### 16.1 Plans (proposed, adjust before launch)

| Plan | Price | Included box-hours | Awake boxes | Projects | Terminals/worktree |
|---|---|---|---|---|---|
| Trial | $0 | 20 | 2 | 2 | 4 |
| Starter | $19/user/mo | 50 | 4 | 5 | 8 |
| Pro | $99/user/mo | 250 | 10 | 10 | 8 |
| Max | $249/user/mo | 600 | 20 | unlimited | 12 |
| Teams | custom | volume | custom | custom | custom |

- Standard box 2 vCPU / 4 GiB / 40 GiB. Pro and above may request 4 vCPU / 8 GiB / 100 GiB.
- Overage: **none at launch.** Hard stop (chosen). Prepaid hour packs can be bought in advance; buying resets the cap immediately.

### 16.2 Metering pipeline

1. `devbox.resume` writes `last_resumed_at`.
2. Heartbeat every 60 s from the reconciler writes a `usage_records` row for the elapsed slice (crash-safe, no double count, uses `last_metered_at`).
3. `devbox.pause` closes the slice, writes the final record, and enqueues `usage.report`.
4. `usage.meter` batches records to Stripe meter events (or invoice line items) keyed by `usage_record.id` for idempotency; sets `reported_to_stripe_at`.
5. `billing.usage` aggregates by workspace + day for the UI; Redis counter for "hours used this period" with Postgres as source of truth on mismatch.

Paused time is never metered, matching the "sleeping boxes are free" expectation. Setup and provisioning time counts as awake (honest, and bounded by a 10 min setup cap).

### 16.3 Hard stop behavior

- Notify at 50% / 80% / 95% (email + in-app).
- At 100%: new worktree creation returns `402 USAGE_LIMIT`; awake boxes are paused after a 5-minute grace (in-flight agent terminals get 5 minutes to finish, then `paused`); running terminals receive a system banner.
- User can buy an hour pack or upgrade; resuming is immediate. Workspace admins can raise the cap in the portal.
- Never terminate a box at 100%: pause only, so work is recoverable.

### 16.4 Entitlement enforcement points

- Worktree create: plan cap, per-project cap, per-box cap.
- Terminal create: per-worktree cap, per-project cap.
- Box create/reset: awake-box cap, project cap.
- Preview routes: per-worktree cap (10 ports).
- Git provider connections: per-workspace cap (5).

---

## 17. API

Base: `/api/v1`. Auth: Clerk JWT (web) or `Authorization: Bearer cng_pat_…` (personal access token). Errors follow the existing `{"message", "detail"}` contract plus a machine `code`.

### 17.1 Projects

| Method | Path | Notes |
|---|---|---|
| `GET` | `/projects` | paginated, filter by status |
| `POST` | `/projects` | `{name, repository_id, provider, default_branch, setup_command, root_path?}` → `202` |
| `GET` | `/projects/{id}` | includes `setup_status`, `box` summary, `worktree_counts` |
| `PATCH` | `/projects/{id}` | name, setup_command, default_branch |
| `DELETE` | `/projects/{id}` | soft delete, pauses box |
| `POST` | `/projects/{id}/setup/run` | retry setup, `202` |
| `GET` | `/projects/{id}/setup/logs` | paginated text, `since_seq` |
| `POST` | `/projects/{id}/box/reset` | destroy + recreate from base image, keep worktree branches |
| `POST` | `/projects/{id}/box/pause` | admin |
| `POST` | `/projects/{id}/box/resume` | admin |

### 17.2 Worktrees

| Method | Path | Notes |
|---|---|---|
| `GET` | `/projects/{id}/worktrees` | includes derived status + terminal counts |
| `POST` | `/projects/{id}/worktrees` | `{title, base_branch, branch?, initial_prompt?, agent?}` → `202` |
| `GET` | `/worktrees/{id}` | |
| `PATCH` | `/worktrees/{id}` | title, status marker (`needs_review`), `pr_url` |
| `DELETE` | `/worktrees/{id}` | `?purge=now` for admin |
| `POST` | `/worktrees/{id}/restore` | within 30-day window |
| `POST` | `/worktrees/{id}/checkout-existing` | `{branch}` |
| `GET` | `/worktrees/{id}/diff` | `git diff base...head` and `--stat` |
| `GET` | `/worktrees/{id}/previews` | list routes |
| `POST` | `/worktrees/{id}/previews/refresh` | re-probe ports |

### 17.3 Terminals

| Method | Path | Notes |
|---|---|---|
| `GET` | `/worktrees/{id}/terminals` | metadata only |
| `POST` | `/worktrees/{id}/terminals` | `{kind, agent?, title?, cols?, rows?}` → terminal |
| `POST` | `/terminals/{id}/attach` | → ticket or `202 waking` |
| `DELETE` | `/terminals/{id}` | kill tmux session |
| `POST` | `/terminals/{id}/restart` | lost sessions |
| `GET` | `/terminals/{id}/transcript` | paginated chunks, `since_seq`, never for `login` |
| `WS` | `/terminals/{id}/stream?ticket=` | §7.4 |
| `POST` | `/terminals/{id}/exec` | one-shot, internal/admin, 60 s cap |

### 17.4 Credentials

| Method | Path | Notes |
|---|---|---|
| `GET` | `/credentials` | own credentials, redacted |
| `POST` | `/credentials` | `{provider, kind:"api_key", value}` |
| `POST` | `/credentials/{provider}/login` | `{worktree_id}` → creates `kind=login` terminal + capture token |
| `POST` | `/credentials/{provider}/verify` | preflight probe |
| `DELETE` | `/credentials/{provider}` | revoke |

### 17.5 Env

| Method | Path |
|---|---|
| `GET/PUT` | `/workspaces/{id}/env` (or `GET /environments?scope=workspace` under tenant) |
| `GET/PUT/DELETE` | `/projects/{id}/env` |

Upsert semantics by key; secret values write-only.

### 17.6 Usage and billing

| Method | Path | Notes |
|---|---|---|
| `GET` | `/usage` | `{period_start, period_end, awake_seconds, by_project[]}` |
| `GET` | `/usage/limits` | caps, current usage, plan |
| existing | `/billing/checkout`, `/billing/portal`, webhooks | reuse Stripe plumbing |

### 17.7 Personal access tokens

| Method | Path |
|---|---|
| `GET/POST` | `/tokens` |
| `DELETE` | `/tokens/{id}` |

### 17.8 Internal endpoints (box-agent, mTLS or box JWT)

| Method | Path | Notes |
|---|---|---|
| `WSS` | `/internal/box/connect` | hello, heartbeat, command channel |
| `WSS` | `/internal/box/tunnel` | preview multiplex |
| `POST` | `/internal/box/{id}/events` | lifecycle + port reports |
| `POST` | `/internal/terminals/{id}/capture` | credential capture (capture token) |
| `POST` | `/internal/terminals/{id}/chunks` | fallback path if gateway unavailable |

### 17.9 Error codes

`PROJECT_NOT_READY`, `BOX_UNAVAILABLE`, `BOX_WAKING`, `WORKTREE_LIMIT`, `TERMINAL_LIMIT`, `USAGE_LIMIT`, `BRANCH_EXISTS`, `GIT_AUTH_FAILED`, `CREDENTIAL_MISSING`, `CAPTURE_TOKEN_INVALID`, `SETUP_FAILED`, `PREVIEW_PORT_CLOSED`, `RESTORE_WINDOW_EXPIRED`.

---

## 18. Database schema

Conventions: UUIDv7 `id` and timestamps from `CommonColumns`; `workspace_id` on every table; `metadata` JSONB; soft delete only where specified. Migrations via Alembic (`make migration name="…"`).

```sql
-- projects: extend existing table
ALTER TABLE projects
  ADD COLUMN repository_id text,
  ADD COLUMN repo_provider text NOT NULL DEFAULT 'github',
  ADD COLUMN default_branch text NOT NULL DEFAULT 'main',
  ADD COLUMN root_path text NOT NULL DEFAULT '',
  ADD COLUMN setup_command text,
  ADD COLUMN setup_status text NOT NULL DEFAULT 'pending',
  ADD COLUMN setup_log_ref text,
  ADD COLUMN base_image_ref text,
  ADD COLUMN created_by text;
-- status enum gains provisioning/setup_failed; keep draft/active/archived mapping

CREATE TABLE devboxes (
  id uuid PRIMARY KEY,
  workspace_id uuid NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  project_id uuid NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  provider text NOT NULL,
  provider_box_id text,
  region text NOT NULL,
  status text NOT NULL,
  image_ref text NOT NULL,
  cpu smallint NOT NULL DEFAULT 2,
  memory_mb integer NOT NULL DEFAULT 4096,
  disk_gb integer NOT NULL DEFAULT 40,
  last_activity_at timestamptz,
  last_resumed_at timestamptz,
  last_metered_at timestamptz,
  paused_at timestamptz,
  awake_seconds_total bigint NOT NULL DEFAULT 0,
  error_code text,
  metadata jsonb NOT NULL DEFAULT '{}',
  created_at timestamptz NOT NULL,
  updated_at timestamptz NOT NULL
);
CREATE UNIQUE INDEX devboxes_one_live_per_project
  ON devboxes(project_id) WHERE status NOT IN ('destroyed','error');
CREATE INDEX devboxes_workspace_status ON devboxes(workspace_id, status);

CREATE TABLE worktrees (
  id uuid PRIMARY KEY,
  workspace_id uuid NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  project_id uuid NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  devbox_id uuid NOT NULL REFERENCES devboxes(id),
  title text NOT NULL,
  initial_prompt text,
  branch text NOT NULL,
  base_branch text NOT NULL,
  base_sha text,
  head_sha text,
  path text NOT NULL,
  slot smallint NOT NULL,
  port_base integer NOT NULL,
  status text NOT NULL DEFAULT 'creating',
  pr_number integer,
  pr_url text,
  merged_at timestamptz,
  created_by text NOT NULL,
  deleted_at timestamptz,
  created_at timestamptz NOT NULL,
  updated_at timestamptz NOT NULL
);
CREATE UNIQUE INDEX worktrees_branch_live ON worktrees(project_id, branch) WHERE deleted_at IS NULL;
CREATE UNIQUE INDEX worktrees_path_live ON worktrees(project_id, path) WHERE deleted_at IS NULL;
CREATE UNIQUE INDEX worktrees_slot_live ON worktrees(project_id, slot) WHERE deleted_at IS NULL;
CREATE INDEX worktrees_status ON worktrees(workspace_id, status) WHERE deleted_at IS NULL;

CREATE TABLE terminals (
  id uuid PRIMARY KEY,
  workspace_id uuid NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  worktree_id uuid NOT NULL REFERENCES worktrees(id) ON DELETE CASCADE,
  devbox_id uuid NOT NULL REFERENCES devboxes(id) ON DELETE CASCADE,
  tmux_session text,
  kind text NOT NULL CHECK (kind IN ('shell','agent','login')),
  agent text,
  title text,
  status text NOT NULL DEFAULT 'starting',
  created_by text NOT NULL,
  credential_id uuid REFERENCES agent_credentials(id) ON DELETE SET NULL,
  recording_enabled boolean NOT NULL DEFAULT true,
  cols smallint NOT NULL DEFAULT 120,
  rows smallint NOT NULL DEFAULT 32,
  last_seq bigint NOT NULL DEFAULT 0,
  scrollback_bytes bigint NOT NULL DEFAULT 0,
  last_attached_at timestamptz,
  closed_at timestamptz,
  created_at timestamptz NOT NULL,
  updated_at timestamptz NOT NULL,
  CONSTRAINT login_never_recorded CHECK (kind <> 'login' OR recording_enabled = false)
);
CREATE UNIQUE INDEX terminals_tmux ON terminals(devbox_id, tmux_session) WHERE tmux_session IS NOT NULL;
CREATE INDEX terminals_worktree ON terminals(worktree_id, status);

CREATE TABLE terminal_chunks (
  terminal_id uuid NOT NULL REFERENCES terminals(id) ON DELETE CASCADE,
  seq bigint NOT NULL,
  content bytea NOT NULL,
  created_at timestamptz NOT NULL
) PARTITION BY RANGE (created_at);
-- monthly partitions; PRIMARY KEY (terminal_id, seq)

CREATE TABLE agent_credentials (
  id uuid PRIMARY KEY,
  workspace_id uuid NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  user_id text NOT NULL,
  provider text NOT NULL,
  kind text NOT NULL,
  secret_ciphertext bytea NOT NULL,
  label text,
  status text NOT NULL DEFAULT 'active',
  expires_at timestamptz,
  last_verified_at timestamptz,
  created_at timestamptz NOT NULL,
  updated_at timestamptz NOT NULL
);
CREATE UNIQUE INDEX one_active_credential ON agent_credentials(user_id, workspace_id, provider)
  WHERE status = 'active';

CREATE TABLE env_vars (
  id uuid PRIMARY KEY,
  workspace_id uuid NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  scope text NOT NULL CHECK (scope IN ('workspace','project')),
  project_id uuid REFERENCES projects(id) ON DELETE CASCADE,
  key text NOT NULL,
  value_ciphertext bytea NOT NULL,
  is_secret boolean NOT NULL DEFAULT true,
  updated_by text NOT NULL,
  created_at timestamptz NOT NULL,
  updated_at timestamptz NOT NULL,
  CHECK ((scope = 'project') = (project_id IS NOT NULL))
);
CREATE UNIQUE INDEX env_unique ON env_vars(workspace_id, scope, COALESCE(project_id, '00000000-0000-0000-0000-000000000000'::uuid), key);

CREATE TABLE preview_routes (
  id uuid PRIMARY KEY,
  workspace_id uuid NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  worktree_id uuid NOT NULL REFERENCES worktrees(id) ON DELETE CASCADE,
  port integer NOT NULL,
  hostname text NOT NULL UNIQUE,
  status text NOT NULL DEFAULT 'pending',
  last_seen_at timestamptz,
  created_at timestamptz NOT NULL,
  updated_at timestamptz NOT NULL
);

CREATE TABLE usage_records (
  id uuid PRIMARY KEY,
  workspace_id uuid NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  project_id uuid NOT NULL,
  devbox_id uuid NOT NULL,
  kind text NOT NULL DEFAULT 'box_awake_seconds',
  quantity bigint NOT NULL,
  period_start timestamptz NOT NULL,
  period_end timestamptz NOT NULL,
  reported_to_stripe_at timestamptz,
  created_at timestamptz NOT NULL
);
CREATE INDEX usage_ws_period ON usage_records(workspace_id, period_start);
CREATE UNIQUE INDEX usage_no_double_count ON usage_records(devbox_id, period_start, period_end);

CREATE TABLE personal_access_tokens (
  id uuid PRIMARY KEY,
  workspace_id uuid NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  user_id text NOT NULL,
  name text NOT NULL,
  token_hash text NOT NULL UNIQUE,
  scopes text[] NOT NULL DEFAULT '{}',
  last_used_at timestamptz,
  expires_at timestamptz,
  created_at timestamptz NOT NULL,
  updated_at timestamptz NOT NULL
);

CREATE TABLE box_events (
  id uuid PRIMARY KEY,
  workspace_id uuid NOT NULL,
  devbox_id uuid NOT NULL REFERENCES devboxes(id) ON DELETE CASCADE,
  type text NOT NULL,
  payload jsonb NOT NULL DEFAULT '{}',
  occurred_at timestamptz NOT NULL
);
```

RLS for every new table:

```sql
ALTER TABLE <t> ENABLE ROW LEVEL SECURITY;
ALTER TABLE <t> FORCE ROW LEVEL SECURITY;
CREATE POLICY workspace_isolation ON <t>
  USING (workspace_id = current_setting('app.current_workspace_id', true)::uuid)
  WITH CHECK (workspace_id = current_setting('app.current_workspace_id', true)::uuid);
```

For `agent_credentials` and `personal_access_tokens`, add:

```sql
CREATE POLICY own_rows ON agent_credentials
  USING (user_id = current_setting('app.current_user_id', true))
  WITH CHECK (user_id = current_setting('app.current_user_id', true));
```

Policies are additive (permissive), so the workspace policy alone would leak across users; the credential tables therefore use the user policy **instead of** a workspace-wide one, with `workspace_id` retained for tenancy joins.

---

## 19. Realtime events

Socket.IO, existing rooms (`workspace:{id}`, `user:{id}`). New events:

| Event | Room | Payload |
|---|---|---|
| `project:setup_progress` | workspace | project_id, phase, percent |
| `project:ready` | workspace | project_id |
| `devbox:status` | workspace | project_id, box_id, status, reason |
| `worktree:created` | workspace | worktree_id, status |
| `worktree:ready` | workspace | worktree_id, branch, terminal_id |
| `worktree:status` | workspace | worktree_id, status, pr_url |
| `worktree:merged` | workspace | worktree_id |
| `worktree:deleted` | workspace | worktree_id |
| `terminal:opened` / `terminal:closed` | workspace | terminal_id, worktree_id, created_by, kind |
| `terminal:lost` | workspace | terminal_id |
| `credential:captured` / `credential:needs_reconnect` | **user** | provider, status |
| `usage:threshold` | workspace | plan, percent, limit |
| `usage:limit_reached` | workspace | period_end, action |

Terminal byte streams do **not** go through Socket.IO; they use the dedicated gateway WS in §7.4. Socket.IO carries state changes only.

---

## 20. Background workers and reconciliation

New RabbitMQ routing keys on the existing `parabox.events` exchange:

```
project.setup.requested / completed / failed
devbox.create.requested / created / paused / resumed / reset / error
worktree.create.requested / created / failed / deleted / purged
terminal.open.requested / closed / lost
env.sync.requested
usage.report.requested
preview.route.registered
```

| Job | Trigger | Behavior |
|---|---|---|
| `setup.runner` | project created / retry | ensure box, exec clone + setup command, stream logs, 10 min cap, mark ready/failed |
| `worktree.runner` | worktree create | ensure awake, allocate slot, git worktree add, env, terminal |
| `devbox.reconciler` | every 15 s + startup | compare desired vs provider state; fix drift; detect dead box-agent (no heartbeat 90 s); mark lost terminals; pause idle boxes (no terminal activity 15 min, no setup running, no manual pin) |
| `usage.meter` | pause / every 60 s | write usage slices, report to Stripe, enforce hard stop |
| `transcript.writer` | Redis stream | batch chunks → Postgres, redaction, retention trim |
| `pr.poller` | every 5 min | update PR state for non-terminal worktrees |
| `purge.runner` | daily 03:00 UTC | hard-delete past retention, remove S3 objects, archive refs cleanup |
| `box.token.sweeper` | every 5 min | expire unused box tokens, revoke orphaned capture tokens |

Idempotency and ordering:

- All jobs keyed by aggregate id; duplicate delivery is safe (state check first, command second).
- Provider calls happen outside DB transactions, with the intent recorded first (`box_events` + `status='provisioning'`). The reconciler is the safety net for crash-after-call.
- One box per project enforced by a partial unique index; the reconciler destroys extra provider boxes it can attribute.
- Dead-letter queue with alerting; 3 retries with exponential backoff (2 s, 10 s, 60 s).

---

## 21. Reliability, observability, and SLOs

### 21.1 SLOs

| SLI | Target |
|---|---|
| API availability | 99.9% monthly |
| API p95 latency (non-streaming) | ≤ 300 ms |
| Terminal echo p95 (same region) | ≤ 150 ms |
| Box resume p95 (warm provider pause) | ≤ 10 s |
| Cold box boot p95 | ≤ 90 s |
| Worktree create p95 (awake box) | ≤ 25 s |
| Reattach success with no lost tail | 99.5% |
| Usage metering accuracy | ±0.5% vs provider invoices |
| Orphaned paid boxes (older than 1 h without a DB row) | 0 |

### 21.2 Observability

- Structured logs (`structlog`) with `workspace_id`, `project_id`, `devbox_id`, `worktree_id`, `terminal_id`, `request_id`; no secrets, no terminal contents.
- OpenTelemetry traces: REST request → job enqueue → provider call → box-agent command, with the terminal WS handshake as its own trace.
- Metrics: box status gauges per provider, awake seconds per workspace, terminal attach latency, WS connections per gateway, chunk lag (Redis → Postgres), job lag, provider error rates, reconciler actions.
- Alerts: any `credential` capture failure, reconciler drift > 5 boxes, gateway CPU > 70%, chunk lag > 30 s, usage reporting failure, setup failure rate > 10%.
- Synthetic checks: create a project box + terminal every 10 min in a canary workspace; alert on failure.

### 21.3 Failure modes and behavior

| Failure | Behavior |
|---|---|
| Provider create fails | retries with backoff; `402/503` to client with `BOX_UNAVAILABLE`; admin notified after 3 |
| Box-agent disconnects | terminals show `reconnecting`; gateway keeps browser sockets 60 s; reattach on hello |
| Box-agent dies | reconciler restarts it via provider exec; if unreachable 5 min, marks box `error` |
| Gateway instance dies | browsers reconnect to another gateway; box stream is shared; no data loss (Redis stream retained 10k chunks) |
| Postgres failover | API returns 503 on write paths; gateway continues streaming (read path independent) but recording buffers in Redis |
| Provider quota exhausted | new boxes rejected with `BOX_UNAVAILABLE`; existing boxes unaffected; alert |
| Clock skew | usage uses DB `now()`; provider times only for reconciliation checks |
| Gemini/unknown agent binary | terminal falls back to shell with a warning; no crash |

### 21.4 Backup / restore

- Postgres PITR (managed), 7-day window; daily logical dump to S3, 30 days.
- Redis is not authoritative; losing it costs reattach history beyond 2 MB, not data.
- Box disks are provider-owned; project reset re-clones from git, so a lost box is recoverable (worktree branches archived in refs are lost unless pushed — documented limitation; UI warns "unpushed work lives only on this box").

---

## 22. Security and threat model

### 22.1 Assets

Agent credentials (Claude/Codex OAuth + API keys), git installation tokens, user source code, env secrets, terminal transcripts, usage/billing data.

### 22.2 Threats and controls

| Threat | Control |
|---|---|
| Tenant reads another tenant's project | RLS on every table + repository filtering; tests assert zero rows under a foreign workspace id |
| User reads another user's agent credential | Per-user credential policy; per-terminal config dirs 0700; capture token bound to terminal + user |
| Credential leaks into transcripts | `login` terminals never recorded; regex redaction on all stored chunks; security event on hit |
| Credential leaks into logs | structlog processors strip `secret`, `token`, `authorization`, `api_key` fields; unit test |
| Stolen terminal ticket | 60 s TTL, single use, bound to user/terminal, HMAC, IP-optional binding |
| Preview URL guessing | unguessable slug + auth cookie + rate limit; no directory listing |
| Agent exfiltrates secrets over network | Egress allowlist per project (provider-enforced where possible, box-agent iptables otherwise); no cloud metadata reachable; deny by default for unknown hosts after onboarding |
| Malicious repo setup script | runs as `dev` (passwordless sudo is a known hole; see residual risks), 10 min cap, egress allowlist, separate audit event, user explicitly triggers setup |
| Runaway agent burns CPU | per-slice nice/cgroup, idle pause, usage hard stop, kill terminal from UI |
| API abuse | per-workspace rate limits (120 rpm REST, 20 terminal creates/min), PAT scopes, Clerk org checks |
| Supply chain | pinned image digests, signed images, `uv.lock`/lockfiles, no `curl | bash` in image build |
| Provider compromise | least-privilege provider API token, per-box tokens short-lived, no cross-workspace provider ids exposed to clients |

### 22.3 Residual risks (accepted, documented)

1. Shared box means a workspace member with shell access can inspect another member's running process env. Mitigation: roles, audit, short-lived per-terminal dirs, future private-box policy.
2. `dev` has passwordless sudo inside the box (needed by many toolchains). Anything in the box is compromised if an agent is prompt-injected; secrets in box memory are reachable.
3. Egress allowlists depend on the provider; local Docker dev has no enforcement.
4. Unpushed work is lost if a box is destroyed without a push or archive.
5. No SOC 2 at launch; security questionnaire answered from this document.

---

## 23. Capacity and performance

Assumptions for launch sizing (1,000 paying users):

- 2,500 projects, 25% awake at peak (≈ 625 boxes), 3,000 concurrent terminals, 1,200 concurrent browser sockets.
- Average box: 2 vCPU / 4 GiB; peak aggregate 1,250 vCPU / 2.5 TiB RAM at the provider (committed capacity negotiated).
- Terminal traffic: 3 KB/s per active terminal × 3,000 = ~9 MB/s egress; gateway fleet of 3 × 4 vCPU handles it with headroom.
- Redis: streams capped 10k chunks/terminal (~200 MB), pub/sub for 625 boxes.
- Postgres: transcripts dominate growth (~60 GB/month at 3,000 terminals × 30 days, compressed); monthly partitions + retention keep the hot table small.

Load tests (must pass before launch):
1. 2,000 concurrent WS terminals on 3 gateway instances, p95 echo ≤ 150 ms.
2. 100 simultaneous worktree creations on one project box (must serialize safely, no duplicate slots).
3. Chunk-writer catch-up after a 10-minute stall without unbounded Redis growth.
4. 500 boxes paused in a 5-minute window; reconciler and metering stay under 30 s lag.

---

## 24. Delivery plan

Each milestone is shippable behind a flag. No milestone starts before the prior one's acceptance criteria pass.

### M0 — Foundation (unblocks everything)

- M0.1 Provider spike: Fly Sprites vs Fly Machines vs E2B against the four blocking questions in §8.2. Decision memo committed.
- New contexts scaffolded: `devbox`, `worktrees`, `terminals`, `credentials`, `previews`, `usage` (domain/application/infrastructure, mirroring `contexts/projects`).
- `app.current_user_id` added to RLS plumbing + tests.
- `DevboxProvider` interface + `local` Docker provider + `make devbox-local`.
- Devbox image v0 + box-agent hello/heartbeat/pty.
- **Acceptance:** `docker compose up`, create a project against the local provider, open a shell terminal in the browser, see output recorded.

### M1 — Projects and onboarding

- GitHub App install flow (extend `contexts/integrations`), GitLab OAuth.
- Project create → box provision → clone → setup → logs → ready.
- Box lifecycle (pause on idle, resume on attach), reconciler v1.
- **Acceptance:** a new user onboards a private repo end to end; killing the API mid-setup recovers without duplicate boxes.

### M2 — Worktrees and terminals

- Worktree create/delete/restore, slot + port allocation, git credential helper, diff endpoint.
- tmux terminals, attach tickets, WS gateway, scrollback replay, transcripts, lost-session handling.
- **Acceptance:** 5 worktrees in parallel on one box, 3 terminals each, browser refresh/reconnect loses nothing in the last 2 MB; kill -9 the gateway and reattach works.

### M3 — Agents and credentials

- Claude + Codex in image, agent terminal launch, login terminal + capture, per-terminal injection, preflight, needs-reconnect, redaction.
- **Acceptance:** Claude Pro user completes `claude login` in a worktree terminal; a new terminal in a different worktree runs the agent without re-login; a second user on the same box never sees the first user's token; transcripts contain no credential material.

### M4 — Env, previews, and collaboration surface

- Env store + materialization + sync.
- Preview routes, wildcard DNS/TLS, tunnel, auth cookie, WS previews.
- Shared terminal viewing, workspace roles enforcement, audit coverage.
- **Acceptance:** two members watch one terminal; a dev server started by an agent is reachable at its preview URL by a teammate, and 404/redirects correctly for outsiders.

### M5 — Billing and limits

- Plans/entitlements, metering slices, Stripe reporting, hard stop, threshold notifications, PATs.
- **Acceptance:** simulated month of usage matches provider invoice within 0.5%; crossing 100% pauses boxes and blocks new worktrees; buying a pack resumes instantly.

### M6 — Production hardening

- Reconciler v2 (drift, orphan cleanup), DLQ, dashboards, alerts, runbooks, chaos tests (provider outage, Redis loss, gateway kill), security review sign-off, GitLab parity audit.
- **Acceptance:** SLOs met in a 48-hour soak; on-call runbook covers the ten most likely incidents; pentest findings triaged.

---

## 25. Testing strategy

- **Unit:** domain state machines, slug/port allocation, usage slicing (no double count), redaction patterns, entitlement math.
- **Repository/RLS:** every new table gets a foreign-workspace and (for credentials) foreign-user zero-row test, following `tests/test_workspaces.py` style.
- **Integration (local Docker provider):** full lifecycle — project create, clone from a local bare repo, setup, worktree add/remove, terminal open/attach/kill, box pause/resume, reconnect.
- **WS contract tests:** frame ordering, replay, seq monotonicity, ticket reuse rejection, resize, backpressure drop.
- **Credential tests:** fake CLI credential file write → capture → encrypt → inject into a second terminal → assert other user's terminal lacks it; assert no plaintext in DB/logs/S3.
- **Metering tests:** simulated clock, pause/resume boundaries, crash between pause and report, idempotent Stripe replay.
- **Failure injection:** kill box-agent, kill gateway, provider 500s, Redis flush, Postgres failover simulation.
- **Load:** §23 scenarios in CI nightly against a staging provider account.
- **E2E (manual + scripted):** the six journeys in §5.

CI additions: `make test` unchanged, plus `make test-integration` (Docker), `make test-load` (nightly), `make lint`, `make typecheck`.

---

## 26. Configuration additions

New `BaseSettings` classes in `src/config/settings.py`:

```python
class DevboxSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="DEVBOX_", extra="ignore")
    provider: Literal["fly", "e2b", "local"] = "local"
    image_ref: str = "ghcr.io/congruence/devbox:dev"
    region: str = "iad"
    default_cpu: int = 2
    default_memory_mb: int = 4096
    default_disk_gb: int = 40
    boot_timeout_seconds: int = 180
    idle_pause_seconds: int = 900
    heartbeat_seconds: int = 15
    idle_check_seconds: int = 15
    max_worktrees_per_project: int = 50
    max_terminals_per_worktree: int = 8
    max_terminals_per_project: int = 32

class FlySettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="FLY_", extra="ignore")
    api_token: str = "placeholder"
    app_name: str = "congruence-devboxes"
    org_slug: str = "congruence"

class E2BSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="E2B_", extra="ignore")
    api_key: str = "placeholder"
    template: str = "congruence-devbox"

class GatewaySettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="GATEWAY_", extra="ignore")
    host: str = "0.0.0.0"
    port: int = 8100
    ticket_secret: str = "placeholder"
    ticket_ttl_seconds: int = 60
    replay_max_bytes: int = 2_097_152
    client_buffer_max_bytes: int = 8_388_608
    max_connections_per_instance: int = 5000

class PreviewSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="PREVIEW_", extra="ignore")
    domain: str = "preview.congruence.dev"
    cookie_secret: str = "placeholder"
    cookie_ttl_seconds: int = 43200
    max_ports_per_worktree: int = 10

class LimitsSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="LIMITS_", extra="ignore")
    trial_hours: int = 20
    starter_hours: int = 50
    pro_hours: int = 250
    max_hours: int = 600
    trial_boxes: int = 2
    starter_boxes: int = 4
    pro_boxes: int = 10
    max_boxes: int = 20
```

Also needed in `.env.example`: Stripe price ids for the four plans, `GATEWAY_TICKET_SECRET`, `PREVIEW_COOKIE_SECRET`, box-agent mTLS material, `CONGRUENCE_CONTROL_PLANE_URL`.

Compose additions: `gateway` service (same image, different command), `preview-edge` (Caddy or a small Go/ASGI proxy), `local-provider` sidecar network, and a `devbox-local` container built from the Devbox image.

---

## 27. Decision log (interview)

| # | Question | Decision | Rejected options / why |
|---|---|---|---|
| 1 | Differentiator | Worktree-first web ADE | Cheaper/BYO compute, team collab layer, local-first — all lose the core product shape |
| 2 | Compute | Hosted sandbox provider, backend first | Own VMs (ops burden), k8s containers (shared kernel), local daemon (breaks the value prop) |
| 3 | Scope of this repo | Backend first; web is a separate consumer | Full-stack in one repo, blueprint-only |
| 4 | Topology | One box per project, N worktrees | Box per worktree (cost/speed), promotable worktree (complexity) — may revisit after launch |
| 5 | Agents | Native CLIs in a PTY, BYO subscription | Direct API harness (rebuilds the harness, resells tokens), hybrid (slower) |
| 6 | Terminal persistence | tmux-backed, reattachable | Ephemeral PTY (kills agents on disconnect) |
| 7 | Repo access | GitHub App + short-lived tokens (GitLab added later per #17) | User PAT (security), public-only (not viable) |
| 8 | Worktree creation | Create from base branch; checkout-existing available post-create | Existing-branch-first (more UI), issue-driven (tracker coupling) |
| 9 | Credential delivery | Encrypted capture + injection | Device-login-only, egress proxy (rejected at launch, proxy noted as future) |
| 10 | Box lifecycle | Persistent + auto-pause | Ephemeral per session, per-worktree clone |
| 11 | Concurrency | Plan-based hard caps | Metered uncapped, queue + auto-start |
| 12 | Isolation | Shared VM, separate dirs | Container per worktree, devcontainers (both deferred); pluggable runtime not chosen |
| 13 | Environment build | Fixed image + per-project setup command | Agent-generated setup plan, per-repo Dockerfile |
| 14 | Previews | Wildcard subdomain per worktree | Path-prefix proxy, defer previews |
| 15 | Secrets | Managed encrypted env store | Uploaded .env, repo-declared only |
| 16 | Box access | Workspace-shared | Private boxes, private + explicit share |
| 17 | Git hosts | GitHub + GitLab | GitHub only (rejected), generic git URL |
| 18 | Merge behavior | Explicit delete, merge hint | Auto-delete, archive-only |
| 19 | Transcripts | Persist everything (chunks) | Live only, agent turns only |
| 20 | Pricing | Seats + included box-hours | Pure usage, free beta |
| 21 | Agents in v1 | Claude Code + Codex | Gemini/OpenCode, config-driven manifest |
| 22 | Entry points | Web + REST API | Slack, CLI, full boxes.dev parity |
| 23 | Spec depth | Implementation-ready, production bar | ADR-only, product brief |
| 24 | Production bar | Single region, HA control plane | Multi-region, single node |
| 25 | Provider | Fly Sprites primary (E2B alternate), local Docker for dev | E2B-first (rejected, kept as alternate), decide-later |
| 26 | Subscription auth | Login inside a worktree terminal; capture the CLI credential file | Dedicated connect box (rejected), API-key-only |
| 27 | Token refresh | Probe on failure → needs_reconnect → prompt | Keeper box, ignore |
| 28 | Login privacy | `login` terminals unrecorded + redaction everywhere | Record-and-redact, record normally |
| 29 | Accounts | One credential per provider per user | Labeled multi-account, workspace service account |
| 30 | Local dev | `local` provider on Docker, same image + agent | Mock provider, real Fly in dev |
| 31 | Shared-box credentials | Per-terminal injection (config dir + env) | Per-box credential, revert to private boxes |
| 32 | Usage limit behavior | Hard stop at 100% (pause, don't destroy); prepaid packs | Warn + auto-buy, warn-only |
| 33 | Latency strategy | Pause/resume + async status UX | Warm pools, cold-start-only |
| 34 | Onboarding sources | Existing repo only (repo root) | Empty project, monorepo subdirs |
| 35 | Name | Congruence | Parabox, neutral codename |
| 36 | Non-goals | Slack/Linear, schedules, mobile apps, self-host, multi-repo, DAGs, in-app PR review, computer-use (no explicit selection; derived from #22 and scope discipline) | — |

---

## 28. Open questions

1. **Provider spike outcome.** If Sprites cannot preserve tmux state across pause (only disk), do we accept cold-reboot UX or switch to Machines + volumes? Owner: platform. Blocks M0.
2. **Price points and included hours.** The numbers in §16.1 mirror market pricing but are unvalidated against provider unit economics. Need a cost model per awake hour at target utilization.
3. **GitLab depth.** OAuth app + project access tokens + MR webhooks is real scope. Confirm GitLab is launch-blocking or can land in M6.
4. **Workspace-shared box vs privacy.** Per-terminal credentials mitigate, but a member can still read another's process env. Do we ship a workspace policy "private boxes" at launch or defer? (Current: defer, documented risk.)
5. **Terminal limit defaults.** 8 per worktree / 32 per project are guesses; needs a real power-user test.
6. **Egress allowlist maintenance.** Who owns the default domain list, and do we allow per-project additions at launch or only "open" mode?
7. **Box reset semantics.** Does reset keep the git repo clone (faster) or rebuild from image + re-clone (cleaner)? Current spec says rebuild + re-clone; confirm cost of setup rerun.
8. **Telemetry / agent status.** Without parsing agent output we cannot show "agent is waiting for permission" precisely. Do we add a lightweight tmux pane-title convention (Claude Code sets the pane title) as a heuristic, or stay hands-off?
9. **PAT + REST API** was implied by "REST API only" but not explicitly requested. Confirm it belongs in launch scope.
10. **Usage enforcement during a running agent.** Hard stop pauses after a 5-minute grace; confirm that a paused box mid-edit is acceptable rather than finishing the current turn.
