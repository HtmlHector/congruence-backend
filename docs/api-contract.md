# Congruence REST & WebSocket API Contract

Base URL: `http://localhost:8000/api/v1` (or `process.env.NEXT_PUBLIC_API_URL`)
WebSocket Gateway: `ws://localhost:8000/api/v1/ws/session/{session_id}`

---

## 1. Projects & Workspaces

### `GET /projects`
* **Response:**
  ```json
  [
    {
      "id": "proj_abc123",
      "name": "My Application",
      "slug": "my-application",
      "repo_full_name": "owner/repo"
    }
  ]
  ```

### `POST /projects/clone`
* **Request:**
  ```json
  {
    "name": "My Repo",
    "repo_url": "https://github.com/owner/repo.git",
    "default_branch": "main"
  }
  ```
* **Response:** `201 Created`
  ```json
  {
    "id": "proj_abc123",
    "name": "My Repo",
    "slug": "my-repo-xyz12",
    "host_id": "host_xyz",
    "pair_lane_id": "lane_pair",
    "status": "cloned"
  }
  ```

### `GET /projects/{project_id}`
* **Response:**
  ```json
  {
    "id": "proj_abc123",
    "name": "My App",
    "slug": "my-app",
    "repo_full_name": "owner/repo",
    "default_branch": "main",
    "host": {
      "id": "host_xyz",
      "state": "awake",
      "backend_type": "process_local"
    },
    "lanes": [
      {
        "id": "lane_pair",
        "name": "Pair lane",
        "slug": "pair-lane",
        "branch_name": "main",
        "is_pair_lane": true,
        "status": "ready"
      }
    ],
    "actors": [
      {
        "id": "act_human",
        "display_name": "You",
        "actor_type": "human",
        "role": "owner",
        "presence": "online"
      }
    ]
  }
  ```

---

## 2. Host Lifecycle (Sleep / Wake)

### `GET /projects/{project_id}/host`
* **Response:**
  ```json
  {
    "id": "host_xyz",
    "project_id": "proj_abc123",
    "state": "awake",
    "backend_type": "process_local",
    "size": "standard",
    "disk_size_gb": 32,
    "awake_seconds_month": 120,
    "last_wake_at": "2026-10-07T12:00:00Z",
    "last_sleep_at": null
  }
  ```

### `POST /projects/{project_id}/host/sleep`
* **Response:**
  ```json
  {
    "status": "asleep",
    "host_id": "host_xyz"
  }
  ```

### `POST /projects/{project_id}/host/wake`
* **Response:**
  ```json
  {
    "status": "awake",
    "host_id": "host_xyz"
  }
  ```

---

## 3. Lanes & Worktrees

### `GET /projects/{project_id}/lanes`
* **Response:** `Array<LaneObject>`

### `POST /projects/{project_id}/lanes`
* **Request:**
  ```json
  {
    "name": "Claude Refactor",
    "slug": "claude-refactor",
    "branch_name": "claude/refactor",
    "is_pair_lane": false
  }
  ```
* **Response:** `201 Created`

---

## 4. Grants & Lease Control (Watch vs Write)

### `GET /projects/{project_id}/lanes/{lane_id}/grant`
* **Response:**
  ```json
  {
    "id": "grnt_123",
    "lane_id": "lane_pair",
    "actor_id": "act_human",
    "permission": "write",
    "is_revoked": false,
    "granted_at": "2026-10-07T12:00:00Z"
  }
  ```

### `POST /projects/{project_id}/lanes/{lane_id}/grant`
* **Request:**
  ```json
  {
    "actor_id": "act_claude",
    "permission": "write"
  }
  ```
* **Response:** `GrantObject`

### `DELETE /projects/{project_id}/lanes/{lane_id}/grant`
* **Response:**
  ```json
  {
    "status": "revoked",
    "lane_id": "lane_pair"
  }
  ```

---

## 5. Services & Preview Reverse Proxy

### `GET /projects/{project_id}/services`
* **Response:**
  ```json
  [
    {
      "id": "srv_3000",
      "lane_id": "lane_pair",
      "port": 3000,
      "protocol": "http",
      "address_subdomain": "preview-lane-pair",
      "access_mode": "private",
      "is_active": true,
      "url": "/preview/lane_pair/3000/"
    }
  ]
  ```

* **Live Preview URL:**  
  Iframe src should point to `${API_BASE}/preview/${lane_id}/${port}/` or relative `/preview/...` via proxy.

---

## 6. Git Status & Diff

### `GET /projects/{project_id}/git/status?lane_id={lane_id}`
### `GET /projects/{project_id}/git/diff?lane_id={lane_id}`
* **Response:**
  ```json
  {
    "lane_id": "lane_pair",
    "branch_name": "main",
    "files_changed": 2,
    "insertions": 15,
    "deletions": 3,
    "diff_text": "diff --git a/src/index.ts b/src/index.ts..."
  }
  ```

---

## 7. GitHub Pull Requests

### `GET /projects/{project_id}/github/pull-requests`
* **Response:**
  ```json
  [
    {
      "id": "pub_123",
      "lane_id": "lane_claude",
      "pr_number": 42,
      "pr_url": "https://github.com/owner/repo/pull/42",
      "title": "Pull Request #42",
      "status": "open",
      "created_at": "2026-10-07T12:00:00Z"
    }
  ]
  ```

### `POST /projects/{project_id}/github/pull-requests`
* **Request:**
  ```json
  {
    "lane_id": "lane_claude",
    "title": "Feat: migrate auth",
    "body": "PR description",
    "target_branch": "main"
  }
  ```
* **Response:**
  ```json
  {
    "pr_number": 42,
    "title": "Feat: migrate auth",
    "status": "open",
    "pushed": true,
    "url": "https://github.com/owner/repo/pull/42",
    "message": "Branch synced"
  }
  ```

---

## 8. Audit Activity Feed

### `GET /projects/{project_id}/activity?limit=20`
* **Response:**
  ```json
  [
    {
      "id": "evt_123",
      "action_type": "project_created",
      "summary": "Project 'My App' initialized",
      "actor_id": "act_human",
      "lane_id": null,
      "timestamp": "2026-10-07T12:00:00Z"
    }
  ]
  ```

---

## 9. WebSocket Protocol (`/api/v1/ws/session/{session_id}`)

* **Client -> Server:**
  - `{"type": "spawn_pty", "lane_id": "...", "command": "/bin/sh", "cols": 80, "rows": 24}`
  - `{"type": "pty_input", "pty_id": "...", "data": "ls\n", "actor_id": "...", "lane_id": "..."}`
  - `{"type": "pty_resize", "pty_id": "...", "cols": 120, "rows": 40}`
  - `{"type": "grant_control", "lane_id": "...", "actor_id": "..."}`
  - `{"type": "revoke_control", "lane_id": "..."}`
  - `{"type": "start_dev", "lane_id": "...", "command": "npm run dev", "port": 3000}`
  - `{"type": "stop_dev", "lane_id": "..."}`
  - `{"type": "sleep_workspace"}`
  - `{"type": "wake_workspace"}`

* **Server -> Client:**
  - `{"type": "handshake_ack", "session_id": "...", "host_state": "awake"}`
  - `{"type": "pty_spawned", "pty_id": "...", "lane_id": "..."}`
  - `{"type": "pty_output", "pty_id": "...", "data": "..."}` (auto rewrites `localhost:3000` -> `/preview/...`)
  - `{"type": "pty_input_denied", "reason": "Lane write lease held by..."}`
  - `{"type": "dev_server_status", "lane_id": "...", "is_running": true, "port": 3000, "url": "..."}`
  - `{"type": "lease_updated", "lane_id": "...", "current_writer": "...", "allow_watchers": true}`
  - `{"type": "host_state_changed", "state": "awake" | "asleep"}`
