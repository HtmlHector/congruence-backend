# Congruence · Technical Requirements Document (TRD)

> **Purpose:** Document system architecture, platform decisions, component boundaries, and performance budgets for Congruence (`congruence.dev`).  
> **Status:** Approved  
> **Systems Architect:** architect  
> **Date:** 2026-10-07  

---

## 1. Technical Strategy & Platform Choices

* **Frontend Framework:** Next.js 15 (App Router), React 19, TypeScript 5.5+
* **Styling Engine:** Tailwind CSS v4 with custom properties (`@theme` / `:root`) adhering to the Hallmark Parchment & Charcoal standard.
* **Component Primitives:** Radix UI unstyled primitives / accessible hooks.
* **Terminal & PTY Simulation:** xterm.js (or lightweight React terminal renderer for POC simulation) with WebLinks addon for automatic port-to-URL rewrites.
* **Control Plane & API:** Next.js Route Handlers / Server Actions deployed to Vercel Edge/Serverless.
* **Database & Persistence:** PostgreSQL (Supabase or Neon) with Drizzle/Prisma ORM.
* **Credential Vault:** Encrypted envelope storage (AES-256-GCM with KMS key rotation) for user-owned CLI credentials (Claude Code OAuth, OpenAI keys, GitHub tokens).
* **Live Session Gateway:** Lightweight Fly.io / Railway WebSocket gateway handling bi-directional PTY multiplexing between browser sessions and host processes.
* **Compute Host:** Fly Sprites (microVM with durable persistent disk, instant sleep, wake-on-request, and public URL routing).
* **Testing Suite:** Playwright (Chromium/Mobile Safari) for multi-device end-to-end verification.

---

## 2. Three-Plane System Architecture

```mermaid
flowchart TD
    subgraph BrowserClient [1. Browser Client (Desktop / Mobile)]
        UI[Workspace Canvas UI]
        PreviewFrame[Private HTTPS Preview Frame]
        TerminalView[xterm.js PTY Terminal]
        LaneManager[Worktree Lane & Actor Leases]
    end

    subgraph ControlPlane [2. Control Plane (Vercel + Postgres)]
        API[Next.js App Router API]
        DB[(PostgreSQL)]
        Vault[Credential Vault KMS]
        GitHubApp[GitHub App Integration]
    end

    subgraph SessionPlane [3. Live Session Gateway (Fly.io)]
        WSGateway[WebSocket Gateway]
        PresenceEngine[Multiplayer Presence & Leases]
    end

    subgraph HostPlane [4. Compute Host (Fly Sprites)]
        SpriteVM[Fly Sprite microVM]
        subgraph SpriteDisk [Persistent NVMe Storage]
            HomeDir["$HOME (Vault Injected Logins & mise Toolchain)"]
            PairLane["/repo (main branch worktree)"]
            AgentLanes["/lanes/<id> (isolated agent/* worktrees)"]
        end
        subgraph SpriteProcesses [Ephemeral Processes]
            PTYMaster["tmux / PTY Session Manager"]
            CLIHarnesses["Claude Code / Codex CLI Processes"]
            DevServer["Dev Server (e.g. Vite / Next on Port 3000)"]
            PortProxy["Port Index & HTTPS Preview Proxy"]
        end
    end

    subgraph ExternalServices [External Source of Truth]
        GitHub[(GitHub Repos & Pull Requests)]
        ModelProviders[(Anthropic / OpenAI Direct Billing)]
    end

    UI --> API
    API --> DB
    API --> Vault
    API --> GitHubApp
    GitHubApp --> GitHub

    UI <--> WSGateway
    WSGateway <--> PTYMaster
    PTYMaster <--> CLIHarnesses
    CLIHarnesses --> ModelProviders

    DevServer --> PortProxy
    PortProxy <--> PreviewFrame
    CLIHarnesses --> AgentLanes
    AgentLanes --> GitHub
```

---

## 3. The `HostBackend` Orchestrator Interface

To ensure harness neutrality and borrowed compute without building custom hypervisors, all compute host interactions are abstracted behind the `HostBackend` interface:

```typescript
export interface HostBackend {
  /** Create or provision a persistent compute host */
  create(options: {
    projectId: string;
    repoUrl: string;
    size: 'small' | 'standard' | 'large';
    networkConfig?: NetworkConfig;
  }): Promise<HostInstance>;

  /** Attach to or spawn a PTY shell for an actor */
  execPty(options: {
    hostId: string;
    laneId: string;
    actorId: string;
    command: string[];
    env: Record<string, string>;
  }): Promise<PtySessionStream>;

  /** Create an isolated Git worktree lane for an agent */
  createLane(options: {
    hostId: string;
    laneId: string;
    branchName: string;
  }): Promise<LaneResult>;

  /** Publish a listening localhost port to a secure HTTPS URL */
  publishPort(options: {
    hostId: string;
    port: number;
    access: 'private' | 'timeboxed-public';
  }): Promise<{ url: string }>;

  /** Suspend compute to near-zero cost while retaining disk */
  sleep(hostId: string): Promise<void>;

  /** Wake host and restore storage mounting */
  wake(hostId: string): Promise<HostStatus>;

  /** Teardown and deprovision host */
  destroy(hostId: string): Promise<void>;
}
```

* **First Planned Backend:** Fly Sprites backend implementation (`SpriteBackend`), leveraging persistent volumes, sub-second wake triggers, and near-zero sleep costs.
* **Secondary Backend:** Railway Sandboxes implementation (`RailwayBackend`) for rapid swarm/lane forking.

---

## 4. Port Indexing & Private HTTPS Proxying

### The Loopback Rewrite Mechanism:
1. When a process (e.g. `pnpm dev`, `vite`, or `next dev`) binds to a port (e.g. `3000`), the in-box host daemon detects the listening socket.
2. The host proxy assigns a subdomain: `https://<project-hash>-<port>.congruence.example`.
3. In the terminal PTY stream, regex matches on `http://localhost:PORT` or `http://127.0.0.1:PORT` and automatically rewrites them into the clickable private HTTPS preview URL.
4. The embedded preview iframe loads the private HTTPS URL with secure cookie-based session auth, ensuring only authorized workspace members can view the preview.

---

## 5. Security, Isolation & Invariant Enforcement

1. **Watch is Default; Write is a Grant:**
   - The WebSocket gateway enforces incoming PTY message filtering.
   - Any client without an active write grant on the current lane has their `stdin` frames dropped at the gateway with an authorization warning.
2. **Account Custody (Never Resell Tokens):**
   - The vault stores OAuth refresh tokens or API keys encrypted with AWS/GCP KMS.
   - On workspace wake, keys are securely injected into `$HOME/.claude.json`, `$HOME/.codex/config.json`, or environment variables in the PTY session.
   - Keys are explicitly excluded from git tracking via global `.gitignore`.
3. **Isolated Worktrees:**
   - Agent lanes are created via `git worktree add /lanes/<id> agent/<task-slug>`.
   - Two autonomous agents can never write to the same directory simultaneously, eliminating race conditions and dirty git index corruption.
4. **Bounded Blast Radius:**
   - Outbound network egress allowlist restricts agent network requests to GitHub, package registries (npm, PyPI), and AI model APIs.
   - Git pushes from agent lanes are restricted to `agent/**` branch prefixes; pushes to `main` require a GitHub Pull Request and human review.

---

## 6. Performance & Reliability Budgets

| Metric | Target | Verification Method |
| :--- | :--- | :--- |
| **Reconnect to Awake Host** | `< 2.0s` | WebSocket handshake & state hydration |
| **Wake Host from Sleep** | `< 12.0s` | Fly Sprite wake-on-request time |
| **PTY Input-to-Render Latency** | `< 30ms` | Local echo & WebSocket roundtrip |
| **Preview Initial Render** | `< 800ms` | Dev server HTTP response through proxy |
| **Client Bundle Size (Gzipped)** | `< 150 kB` | Next.js build bundle analyzer |
| **Test Verification** | 100% of P0 Features | Playwright end-to-end test suite |
