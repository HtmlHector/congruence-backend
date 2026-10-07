# Congruence · Product Requirements Document (PRD)

> **Purpose:** Define what Congruence must do and how success will be measured.  
> **Status:** Approved  
> **Owner / Originator:** Parabox Product Studio  
> **Idea Scientist:** idea-scientist  
> **Date:** 2026-10-07  

---

## 1. Product Name & One-Sentence Idea
* **Product Name:** Congruence (`congruence.dev`)
* **One-Sentence Idea:** A shared browser workspace for the coding agents you already use—keeping identity, files, the CLI harnesses people already pay for, and the live process that proves the change in one system, reachable from any browser without rebuilding context.
* **Core Product Thesis:** Claude Code can run in the cloud, but cannot show you the application it just started. Conductor runs harnesses and tunnels the site to a Mac app. Codespaces is an editor attached to a repository. Congruence is the host where the harness, the git worktrees, and the running app stay together in a browser.

---

## 2. Target Users & Buyer Profile
* **Primary Buyer & User:** 2–15 person engineering and product teams who already pay for Anthropic Claude Max, OpenAI ChatGPT / Codex, or Cursor, and treat GitHub as their source of truth. They routinely run ~2.3 AI coding tools simultaneously.
* **Observed Pains:**
  1. **Device Lock:** The repo is cloned locally, the CLI is logged in on one machine, and switching machines requires re-cloning, re-authenticating, or managing fragile SSH/tmux sessions.
  2. **The "Loopback Hole":** Cloud agent sessions (e.g. Claude Code cloud) print `http://localhost:PORT` as dead text; they cannot display or verify the running front-end preview.
  3. **Agent Collisions:** Running two harnesses in one local checkout results in overwritten files and dirty tree race conditions.
  4. **The Preview Tax:** Product managers and reviewers cannot inspect WIP changes without opening a pull request and waiting 20–30 minutes for a CI preview environment.
* **Secondary Personas:** Product Managers, Designers, and Technical Leads who need to observe active terminal sessions ("watch first") and interact with live private preview URLs from a phone, tablet, or secondary laptop without needing write access or local toolchains.
* **Explicit Non-Buyers (Year One):** Fortune 100 enterprise requiring on-prem VPC/SCIM/air-gap (served by Coder/Ona Enterprise); solo hobbyists wanting turnkey Replit generators; pure Cursor-only shops; teams whose only metric is an unattended bot opening a PR overnight.

---

## 3. Product Vocabulary & The Four States

| State | Industry Default | Congruence Architecture |
| :--- | :--- | :--- |
| **Who You Are** | Fragmented vendor sites and local keychains | **Vault** and project membership |
| **Files** | Single local device checkout | **Host** disk and isolated **Lanes** (git worktrees) |
| **Agency** | Local CLI on a single laptop | Concurrent **Actors** (human or CLI harness) on the host |
| **Result** | Localhost on that machine or an expensive CI deploy | Private, authenticated **Service** address (`https://...`) |

### Canonical Domain Vocabulary:
* **Project:** The workspace context tied to a GitHub repository, containing hosts, lanes, and services.
* **Host:** The underlying compute machine (Fly Sprite microVM) with durable disk, wake-on-request, and near-zero sleep cost.
* **Lane:** An isolated git worktree (`pair lane`, `claude/progress`, `codex/copy`). Two writers never share a dirty tree unless explicitly placed together.
* **Actor:** A human participant or an autonomous CLI harness (`claude`, `codex`, `opencode`, `aider`).
* **Service:** A process listening on a port, published as a private, authenticated HTTPS URL.
* **Grant:** Explicit, scoped, revocable write leases granted to an actor on a specific lane. Watch is the default; write is a grant.
* **Publication:** The pull request flow back to GitHub. GitHub remains the single source of truth.
* **Vault:** Credential storage that securely injects tokens into `$HOME` on the host without exposing secrets to raw git trees.

---

## 4. The 11 Core Invariants
1. **Continuity:** Close the tab or laptop. Files, harness logins, and toolchain remain. Compute may sleep.
2. **A Browser is Enough:** No required companion app or desktop client.
3. **Harness Neutrality:** Congruence does not write an agent loop. It hosts `claude`, `codex`, `opencode`, `aider`, and a shell.
4. **Account Custody:** The vault injects into `$HOME`. Anthropic and OpenAI still bill the user directly. We never resell tokens or tax inference.
5. **Concurrent Actors:** N humans and M harnesses can collaborate in one project.
6. **Isolated Mutation:** Two writers do not share a dirty tree unless someone puts them in the same lane. A collision is our bug.
7. **Addressable Result:** If a process listens, it has a private HTTPS URL every authorized team member can open.
8. **Watch is the Default; Write is a Grant:** Anyone can observe; only granted actors can execute or edit.
9. **Publication is Git:** A GitHub App opens standard pull requests. No proprietary secondary version control.
10. **Bounded Blast Radius:** Egress allowlist, spend caps, scoped `agent/**` branch pushes.
11. **Borrowed Compute:** The host lives behind a clean `HostBackend` orchestrator interface. We do not maintain custom hypervisors.

---

## 5. Goal & Success Measure
* **Desired Outcome:** A developer or product lead signs in with GitHub, wakes their project in seconds, runs their dev server, opens a live preview on their phone, and steers their coding agent without re-authenticating or risking write collisions.
* **Measurable Signal:**
  - **Wake Latency:** < 5s to reconnect to an awake workspace; < 15s to wake from sleep.
  - **Device Parity:** 100% functional preview and terminal interaction on mobile and desktop browsers.
  - **Zero Silent Overwrites:** 0% cross-actor write collision incidents due to worktree isolation.
  - **The 20-Second Film Test:** A continuous 20-second video demonstrating: click GitHub button → open repo → Claude starts dev server → tap phone → live running app.
* **Falsification Criteria:** 
  - If users prefer managing local tmux over SSH.
  - If Claude Web or Conductor solves live browser preview without a Mac client.

---

## 6. Parabox Evidence Gates
* [x] **Gate 1: User Input & Behavior:** Verified developer frustration with Claude Cloud issue #58255 (closed as "not planned"—loopback port 3000 cannot be inspected); validated high multi-harness usage (developers running 2.3 agents simultaneously needing isolated worktrees).
* [x] **Gate 2: Proof of Concept Boundary:** 
  - Fly Sprite host with durable disk and wake-on-request.
  - Integrated browser canvas: Pair lane + agent lanes (`claude`, `codex`), Terminal, live HTTPS preview proxy, and Changes diff.
  - Watcher vs Writer grant mechanism.
  - Sleep/wake continuity retaining files and tool identities.
  - Self-contained interactive concept simulation running on `congruence.dev`.
  - *Out of Scope for POC:* Heavy custom Monaco editor clone, proprietary agent model, billing for AI tokens.
* [x] **Gate 3: Concrete Commitment:** Commercial willingness-to-pay anchored at $49/mo per writer (watchers free); verified with automated Playwright end-to-end tests validating lane switching, preview proxying, and write lease controls.

---

## 7. Core Features & Roadmap
| Feature | User Benefit | Priority (P0 / P1 / P2) |
| :--- | :--- | :--- |
| **Multi-Lane Worktrees** | Isolates each human or agent to `/lanes/<id>` and dedicated `agent/*` branches. | P0 (POC) |
| **Private Service Addresses** | Automatically rewrites `localhost:PORT` to authenticated HTTPS URLs accessible on any device. | P0 (POC) |
| **Watch First / Scoped Write Grants** | Collaborators and agents watch read-only until the owner grants a revocable write lease. | P0 (POC) |
| **Workspace Continuity (Sleep/Wake)** | Persistent disk preserves repo, mise toolchains, and `$HOME` logins across sleep cycles. | P0 (POC) |
| **Interactive Concept Preview** | High-fidelity simulated workspace on `congruence.dev` for rapid product evaluation. | P0 (POC) |
| **GitHub App Integration** | Seamless clone, webhook branch sync, and direct `gh pr create` publication. | P1 (Pilot) |
| **Vault Credential Injection** | Retains Claude Code, Codex, and Git credentials securely in `$HOME` without git leaks. | P1 (Pilot) |
| **WebSocket Session Gateway** | Low-latency PTY streaming and terminal presence. | P1 (Pilot) |
| **Spend Visibility & Egress Limits** | Explicit display of awake host cost; bounded network blast radius. | P2 (Scale) |
| **Pluggable HostBackend** | Railway Sandboxes for swarm/lane forks; custom docker sandbox backends. | P2 (Scale) |

---

## 8. Out of Scope for Version One
* **Proprietary Agent Brain:** Congruence orchestrates standard CLI harnesses; it does not build or sell a model.
* **Token Resale / Inference Metering:** Users bring their existing Claude Max, ChatGPT Plus, or API keys.
* **Full Cloud IDE Editor:** The editor is an optional view; harnesses and terminals edit the disk.
* **Live Process Memory Across Sleep:** Running RAM processes (`npm run dev`) are ephemeral; disk, git, and credentials survive.

---

## 9. Business Model & Pricing
* **Principle:** Charge for the seat and the slept host. Never charge for tokens or inference.
* **Free Tier:** 1 small workspace, automatic idle sleep, private preview URL, capped awake hours.
* **Pro Tier ($49/month per writer):**
  - Unmetered write access for licensed users.
  - Unlimited free read-only watchers and preview guests.
  - Host compute passed through at Sprite cost (~$0.07/CPU-hr, ~$0.04/GB-hr, ~$8–15/mo for typical 4hr/day use).
  - Clear real-time spend indicator: e.g., *"Box $0.12 today (8h awake)"*.

---

## 10. User Stories & Acceptance Criteria

### Story 1: Wake and Inspect from a Mobile Browser
* **As a** developer or product lead away from my primary computer,
* **I want to** open my project URL on my phone or tablet,
* **So that** I can wake the host, observe the agent's progress in the terminal, and interact with the running preview.
* **Acceptance Criteria:**
  - Given a sleeping workspace, navigating to the URL displays an awake transition (< 15 seconds).
  - Terminal output and live HTTPS preview render responsively in mobile Safari/Chrome without horizontal breakage.

### Story 2: Run Parallel Agents in Isolated Worktrees
* **As an** engineer running Claude Code on an API refactor and Codex on copy edits,
* **I want to** launch each harness in its own dedicated lane,
* **So that** neither agent clobbers the other's uncommitted files.
* **Acceptance Criteria:**
  - Adding a new agent lane creates an isolated git worktree at `/lanes/<id>` on a separate `agent/*` branch.
  - Each lane maintains its own PTY session and terminal tab.

### Story 3: Grant and Revoke Scoped Lane Control
* **As a** workspace owner pairing with a teammate or testing an autonomous CLI,
* **I want to** keep them in read-only observation mode until I grant write control,
* **So that** only authorized actors can input commands or mutate code.
* **Acceptance Criteria:**
  - Watchers see real-time streaming output but cannot send stdin to the terminal.
  - Clicking "Grant control" transfers active typing lease to that actor; clicking "Revoke" instantly returns write lease to the owner.

### Story 4: Automatic Port Publishing
* **As a** developer or agent executing `pnpm dev`,
* **I want** the local listening port (e.g. 3000) to automatically publish as an authenticated HTTPS URL,
* **So that** I can immediately open the app without configuring SSH port forwarding.
* **Acceptance Criteria:**
  - Terminal output detection or port index detects port binding and renders the preview iframe at `https://<project>-<port>.congruence.example`.
  - Preview updates automatically when edits occur.
