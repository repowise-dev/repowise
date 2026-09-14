# REST API Reference

The `repowise serve` command starts a FastAPI backend on port `7337` (default).
All endpoints are prefixed with `/api/` and return JSON unless otherwise noted.

## Authentication

Set `REPOWISE_API_KEY` to require bearer-token auth on every non-`/health`
endpoint. Pass it as an `Authorization` header:

```http
Authorization: Bearer <your-key>
```

Without a key set, the server accepts all local callers and rejects remote
callers with `403 Forbidden`.

## Common error shapes

| Status | When |
|--------|------|
| `400` | Invalid body or query parameter |
| `403` | Missing or invalid API key |
| `404` | Resource not found |
| `409` | Conflict — a job is already running for this repository |
| `422` | Unprocessable entity (validation error) |
| `500` | Internal server error |

Error responses have a `{"detail": "..."}` body.

---

## System

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/health` | Liveness + readiness check. Returns `{"status": "ok"}`. Not auth-gated. |
| `GET` | `/metrics` | Prometheus-compatible counters (job counts, token totals, stale-page count). Not auth-gated. |
| `GET` | `/api/version` | Server version string and build metadata. |
| `GET` | `/api/changelog` | Latest changelog entries from the configured source. |

---

## Repositories (`/api/repos`)

### Repository object

```json
{
  "id": "uuid",
  "name": "my-repo",
  "local_path": "/home/user/my-repo",
  "url": "https://github.com/org/my-repo",
  "default_branch": "main",
  "head_commit": "abc123",
  "settings": {},
  "workspace_alias": null,
  "workspace_status": "indexed",
  "docs_enabled": true,
  "docs_mode": "full",
  "run_mode": null,
  "git_tier": "full",
  "created_at": "2025-01-01T00:00:00Z",
  "updated_at": "2025-01-01T12:00:00Z"
}
```

### Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/repos` | Register a new repository (or update if `local_path` already exists). Returns `201` with the repo object. Enqueues an initial index job by default (`index: true`). |
| `GET` | `/api/repos` | List all registered repositories. In workspace mode, includes synthetic entries for workspace repos not yet indexed (`workspace_status: "needs_index"` or `"missing_dir"`). |
| `GET` | `/api/repos/summary` | Single-call payload for the multi-repo dashboard — replaces `N` separate `/stats` + `/git-summary` calls. Returns aggregate stats (file count, page count, hotspots, health score, freshness) for every repo in one response. |
| `GET` | `/api/repos/{repo_id}` | Get a single repository by ID. |
| `PATCH` | `/api/repos/{repo_id}` | Update repository fields (`name`, `url`, `default_branch`, `settings`). |
| `DELETE` | `/api/repos/{repo_id}` | Delete a repository and all its data (pages, jobs, graph, git metadata). Returns `{"ok": true, "deleted_pages": N}`. |
| `GET` | `/api/repos/{repo_id}/stats` | Aggregate stats for one repo: file count, symbol count, entry-point count, doc coverage %, freshness score, dead-export count. |
| `POST` | `/api/repos/{repo_id}/sync` | Trigger incremental documentation sync (git diff since last commit → regenerate affected pages). Returns `202` with `{"job_id": "...", "stream_token": "..."}`. `409` if a job is already running. |
| `POST` | `/api/repos/{repo_id}/full-resync` | Trigger full regeneration of all wiki pages and vector embeddings. Returns `202`. `409` if a job is already running. |
| `POST` | `/api/repos/{repo_id}/generate` | Generate (or regenerate) a subset of wiki pages. Body selects pages by `kind` (`all`, `unwritten`, `stale`, `page_ids`, `path_prefix`, `ranked`) and optional `cascade` (`none`, `dependents`, `full`). Returns `202`. |
| `POST` | `/api/repos/{repo_id}/generate/estimate` | Same body as `/generate`; returns a cost estimate (tokens + USD) without running. |
| `POST` | `/api/repos/{repo_id}/index` | Run the structural index (parse, graph, git, dead code) without generating wiki pages. Returns `202`. |
| `POST` | `/api/repos/{repo_id}/preflight` | Validate a prospective index configuration (provider key, model, embedder) before committing to an index run. |
| `GET` | `/api/repos/{repo_id}/export` | Export the wiki as a zip archive. Query params: `format` (`markdown`, `html`, `json`), `path_prefix`. |
| `GET` | `/api/repos/{repo_id}/file-content` | Return raw source content of a file in the repo. Query params: `path` (required). |

**`POST /api/repos` request body:**

```json
{
  "name": "my-repo",
  "local_path": "/home/user/my-repo",
  "url": "https://github.com/org/my-repo",
  "default_branch": "main",
  "settings": {},
  "index": true
}
```

**`POST /api/repos/{repo_id}/generate` request body:**

```json
{
  "selection": {
    "kind": "unwritten"
  },
  "cascade": "dependents",
  "style": null
}
```

`kind` options:
- `all` — every page in the repo
- `unwritten` — pages that have never been model-written (default)
- `stale` — pages below the freshness threshold
- `page_ids` — explicit list of page IDs (`page_ids: [...]`)
- `path_prefix` — all pages under a path prefix (`path_prefix: "src/auth/"`)
- `ranked` — highest-importance pages up to `coverage_pct` (fraction, e.g. `0.2` = top 20%) or `top_n`

---

## Jobs (`/api/jobs`)

Background jobs are created by sync and generate endpoints. Track their
progress via SSE.

### Job object

```json
{
  "id": "uuid",
  "repository_id": "uuid",
  "status": "running",
  "config": {"mode": "incremental"},
  "progress": 45,
  "total": 120,
  "error": null,
  "created_at": "2025-01-01T00:00:00Z",
  "updated_at": "2025-01-01T00:01:00Z"
}
```

`status` values: `pending`, `running`, `completed`, `failed`, `cancelled`.

### Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/jobs` | List recent jobs. Query params: `repo_id` (filter), `limit` (default: 20). |
| `GET` | `/api/jobs/{job_id}` | Get a single job by ID. |
| `POST` | `/api/jobs/{job_id}/cancel` | Cancel a running job. |
| `GET` | `/api/jobs/{job_id}/stream` | **SSE** — live job progress stream. Events: `progress` (pages done/total, current file, tokens, estimated cost), `done`, `error`. Requires `stream_token` query param (minted alongside `job_id` by the launch endpoint) or the standard `Authorization` header. |

**SSE event shape:**

```text
event: data
data: {"type": "progress", "done": 45, "total": 120, "current_file": "src/auth.py", "tokens": 12000, "cost_usd": 0.04}

event: data
data: {"type": "done", "job_id": "uuid"}
```

---

## Pages (`/api/pages`)

### Page object

```json
{
  "id": "uuid",
  "repository_id": "uuid",
  "title": "auth module",
  "content_md": "...",
  "page_type": "file",
  "source_path": "src/auth.py",
  "confidence": 0.92,
  "freshness_status": "fresh",
  "notes": null,
  "created_at": "2025-01-01T00:00:00Z",
  "updated_at": "2025-01-01T12:00:00Z"
}
```

### Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/pages` | List pages for a repo. Query params: `repo_id` (required), `page_type`, `freshness_status`, `fields` (`summary` omits `content_md` for fast listing, `full` includes it), `limit`, `offset`. |
| `GET` | `/api/pages/lookup` | Look up a page by `repo_id` + `source_path`. Returns the single matching page. |
| `GET` | `/api/pages/lookup/versions` | Version history for a page identified by `repo_id` + `source_path`. |
| `PATCH` | `/api/pages/lookup/notes` | Update user notes on a page (identified by `repo_id` + `source_path`). Body: `{"notes": "..."}`. |
| `POST` | `/api/pages/lookup/regenerate` | Enqueue a regeneration job for a single page (by `repo_id` + `source_path`). Returns `202`. |
| `GET` | `/api/pages/{page_id}` | Get a single page by ID. |

---

## Search (`/api/search`)

Hybrid search combining vector similarity (LanceDB or pgvector) and full-text
(SQLite FTS5 / PostgreSQL `tsvector`).

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/search` | Search wiki pages. Query params: `q` (query), `repo_id`, `mode` (`semantic`, `fulltext`, `hybrid`), `limit` (default: 10). Returns ranked `SearchResultResponse` list. |

**Response item shape:**

```json
{
  "page_id": "uuid",
  "title": "auth module",
  "snippet": "...relevant excerpt...",
  "score": 0.87,
  "source_path": "src/auth.py",
  "page_type": "file"
}
```

---

## Symbols (`/api/symbols`)

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/symbols` | Paginated symbol index. Query params: `repo_id` (required), `q` (name filter), `kind` (`function`, `class`, `variable`, etc.), `file_path`, `sort` (`pagerank`, `name`), `limit`, `offset`. |
| `GET` | `/api/symbols/detail` | Rich symbol card — source, callers, callees, page link, health markers. Query params: `repo_id`, `symbol_id` or `name` + `file_path`. |
| `GET` | `/api/symbols/by-name/{name}` | All symbols matching a bare name across a repo. |
| `GET` | `/api/symbols/{symbol_db_id}` | Get a single symbol by its database ID. |

---

## Git & Code Intelligence (`/api/repos/{repo_id}/…`)

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/{repo_id}/commits` | Paginated commit list. Query params: `since`, `until`, `author`, `path`, `limit`. |
| `GET` | `/{repo_id}/commits/agents` | Commits attributed to AI agents (detected by commit message patterns). |
| `GET` | `/{repo_id}/commits/stats` | Aggregate commit statistics: total, authors, churn, frequency histogram. |
| `GET` | `/{repo_id}/commits/evolution` | Commit frequency time series for trend charts. |
| `GET` | `/{repo_id}/commits/{sha}` | Single commit details (files changed, message, author, stats). |
| `GET` | `/{repo_id}/git-metadata` | Per-file git metadata (churn, ownership, last_commit, hotspot bit). Query params: `path_prefix`, `limit`, `offset`. |
| `GET` | `/{repo_id}/hotspots` | Top-churn + top-complexity files ranked by composite hotspot score. Query params: `limit`, `include_health`. |
| `GET` | `/{repo_id}/ownership` | File / module / package ownership breakdown. Query params: `granularity` (`file`, `module`, `package`), `path_prefix`. |
| `GET` | `/{repo_id}/co-changes` | Co-change partners for a file. Query params: `path` (required), `limit`. |
| `GET` | `/{repo_id}/risk/range` | Change risk assessment for a commit range (`base..head`). Query params: `base`, `head`. |
| `GET` | `/{repo_id}/git-summary` | Aggregate git health signals: hotspot count, total commits, bus-factor files, active authors. |
| `GET` | `/{repo_id}/stats/highlights` | KPI highlights for the overview dashboard (top language, top owner, freshest module, riskiest file). |

---

## Dead Code (`/api/repos/{repo_id}/dead-code` and `/api/dead-code`)

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/{repo_id}/dead-code` | List dead code findings. Query params: `kind` (`unreachable_file`, `unused_export`, `unused_internal`, `zombie_package`), `status` (`open`, `resolved`, `acknowledged`), `min_confidence`, `safe_only`, `path_prefix`, `limit`, `offset`. |
| `POST` | `/{repo_id}/dead-code` | Trigger a full dead-code analysis for the repo. Returns `202` with `job_id`. |
| `GET` | `/{repo_id}/dead-code/summary` | Aggregate finding counts by kind and confidence tier. |
| `PATCH` | `/api/dead-code/{finding_id}` | Update a finding's status. Body: `{"status": "resolved"}` or `{"status": "acknowledged", "note": "..."}`. |

---

## Code Health (`/api/repos/{repo_id}/…`)

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/{repo_id}/health/overview` | Latest health snapshot: overall score (1–10), defect / maintainability / performance sub-scores, per-file scores, trend vs. previous snapshot. |
| `GET` | `/{repo_id}/health/coverage` | Coverage ingestion status and per-file test-coverage data (requires a coverage report to have been ingested). |

---

## Architectural Decisions (`/api/repos/{repo_id}/decisions`)

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/{repo_id}/decisions` | List all decisions. Query params: `lane` (`active`, `superseded`, `candidate`), `q` (full-text filter), `limit`, `offset`. |
| `GET` | `/{repo_id}/decisions/lane-counts` | Count of decisions per lifecycle lane. |
| `GET` | `/{repo_id}/decisions/health` | Decision health dashboard: stale decisions, unlinked files, validation issues. |
| `GET` | `/{repo_id}/decisions/graph` | Decision dependency graph (nodes = decisions, edges = supersedes/refines). |
| `GET` | `/{repo_id}/decisions/{decision_id}` | Single decision record with full body, linked files, and history. |
| `GET` | `/{repo_id}/decisions/{decision_id}/alignment` | Alignment check — which files implement or violate this decision. |
| `PUT` | `/{repo_id}/decisions/{decision_id}` | Replace a decision record (full update). |
| `POST` | `/{repo_id}/decisions` | Create a new decision record. |
| `PATCH` | `/{repo_id}/decisions/{decision_id}` | Partial update (lane change, note, link/unlink files). |

---

## Episodes (`/api/repos/{repo_id}/episodes`)

Episodes are mined agent-session transcripts (decision events, key tool calls).

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/{repo_id}/episodes` | List episodes. Query params: `kind` (`decision`, `all`), `limit`, `offset`. |
| `GET` | `/{repo_id}/episodes/count` | Episode count (fast; no body). |
| `GET` | `/{repo_id}/episodes/by-file` | Episodes attributed to a specific file. Query params: `path` (required). |
| `GET` | `/{repo_id}/episodes/{episode_id}` | Single episode with full event log. |

---

## Refactoring (`/api/repos/{repo_id}/refactoring`)

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/{repo_id}/refactoring/targets` | Ranked refactoring opportunities (file-level). Query params: `kind` (`complexity`, `duplication`, `coupling`), `limit`. |
| `GET` | `/{repo_id}/refactoring/targets/page` | LLM-written refactoring plan page for one file. Query params: `path` (required). |
| `GET` | `/{repo_id}/refactoring/summary` | Aggregate refactoring metrics: total opportunities, estimated effort, top file. |
| `GET` | `/{repo_id}/refactoring/settings` | Current per-repo refactoring settings. |
| `PUT` | `/{repo_id}/refactoring/settings` | Update refactoring settings (enable/disable LLM plans, effort threshold). |
| `GET` | `/{repo_id}/refactoring/{suggestion_id}` | Full refactoring plan for one opportunity, including LLM-generated explanation. |
| `PATCH` | `/{repo_id}/refactoring/{suggestion_id}` | Update a suggestion's status (`accepted`, `dismissed`). |
| `POST` | `/{repo_id}/refactoring/{suggestion_id}/generate-code` | Generate a concrete refactored code diff for this suggestion. Returns `202`. |

---

## C4 Architecture Diagrams (`/api/graph/{repo_id}/c4`)

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/graph/{repo_id}/c4/l1` | L1 System Context diagram data (systems, users, external dependencies). |
| `GET` | `/api/graph/{repo_id}/c4/l2` | L2 Container diagram data (services, databases, message brokers). |
| `GET` | `/api/graph/{repo_id}/c4/l3` | L3 Component diagram data (components within a container). Query params: `container` (required). |
| `GET` | `/api/graph/{repo_id}/c4/structurizr` | Full Structurizr DSL export as plain text. |
| `GET` | `/api/graph/{repo_id}/c4/mermaid` | Mermaid diagram source string. Query params: `level` (`l1`, `l2`, `l3`). |

---

## Workspace (`/api/workspace`)

Multi-repo workspace endpoints. Only available when `repowise serve` is started
with a `workspace.yaml`.

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/workspace` | Workspace overview: repos, shared dependencies, overall health. |
| `GET` | `/api/workspace/contracts` | Cross-repo API contract links (which repo exports what API surface to which consumers). |
| `GET` | `/api/workspace/contracts/detail` | Full schema for one contract. Query params: `provider`, `consumer`. |
| `GET` | `/api/workspace/co-changes` | Cross-repo temporal co-change partners. |
| `GET` | `/api/workspace/graph` | Workspace dependency graph in D3-compatible JSON. |
| `GET` | `/api/workspace/system-graph` | System-level dependency graph (service → service edges). |
| `GET` | `/api/workspace/diagnostics` | Workspace extraction health: parsing errors, missing contracts, stale repos. |
| `GET` | `/api/workspace/blast-radius` | Cross-repo blast radius for a change. Query params: `repo_id`, `path`. |
| `GET` | `/api/workspace/breaking-changes` | Incompatible cross-repo contract report (breaking API changes). |
| `GET` | `/api/workspace/conformance` | Architectural boundary conformance: which repos violate defined layer rules. |
| `GET` | `/api/workspace/architecture` | System score and role metrics per service. |
| `POST` | `/api/workspace/sync` | Sync all stale repos in the workspace. Returns `202` with a list of `job_id`s. |

---

## Codebase Chat (`/api/repos/{repo_id}/chat`)

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/{repo_id}/chat/messages` | **SSE** — send a message and stream the agentic response. Body: `{"message": "...", "conversation_id": null, "provider": null, "model": null, "context": null}`. Response is `text/event-stream`. |
| `GET` | `/{repo_id}/chat/conversations` | List conversations for a repo (newest first). |
| `GET` | `/{repo_id}/chat/conversations/{conversation_id}` | Get a conversation with its full message history. |
| `PATCH` | `/{repo_id}/chat/conversations/{conversation_id}` | Update a conversation (`title`, `pinned`). |
| `DELETE` | `/{repo_id}/chat/conversations/{conversation_id}` | Soft-delete a conversation. |
| `POST` | `/{repo_id}/chat/conversations/{conversation_id}/restore` | Restore a soft-deleted conversation. |
| `POST` | `/{repo_id}/chat/conversations/{conversation_id}/fork` | Fork a conversation at a given message. Body: `{"through_message_id": null, "before_message_id": null}`. |
| `GET` | `/{repo_id}/chat/conversations/{conversation_id}/artifacts/{artifact_id}` | Get a tool artifact from a conversation message. |
| `PATCH` | `/{repo_id}/chat/conversations/{conversation_id}/artifacts/{artifact_id}` | Pin or unpin an artifact. Body: `{"pinned": true}`. |

**SSE chat event types:**

| Type | Payload |
|------|---------|
| `text_delta` | `{"type": "text_delta", "text": "..."}` |
| `tool_start` | `{"type": "tool_start", "tool_id": "...", "tool_name": "...", "input": {...}}` |
| `tool_result` | `{"type": "tool_result", "tool_id": "...", "tool_name": "...", "summary": "...", "artifact": {...}}` |
| `done` | `{"type": "done", "conversation_id": "...", "message_id": "...", "provider": "...", "model": "..."}` |
| `error` | `{"type": "error", "message": "..."}` |

---

## Provider Management (`/api/providers`)

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/providers` | Current provider status (active provider, model, key presence). |
| `PATCH` | `/api/providers/active` | Switch the active provider/model at runtime (persists per repo). Body: `{"provider": "openai", "model": "gpt-4o"}`. |
| `POST` | `/api/providers/{provider_id}/key` | Store an API key for a provider. Body: `{"key": "sk-..."}`. |
| `DELETE` | `/api/providers/{provider_id}/key` | Remove a stored API key. |
| `POST` | `/api/providers/{provider_id}/validate` | Validate a provider key + model by making a test call. Returns `{"valid": true}`. |

---

## MCP Tool Surface (`/api/mcp`)

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/mcp/tools` | Current MCP tool surface — which tools are active, which are disabled, and their profiles. |
| `PATCH` | `/api/mcp/tools` | Enable or disable specific MCP tools by name. Body: `{"enable": ["get_health"], "disable": ["get_dead_code"]}`. |

---

## CLAUDE.md Generation (`/api/repos/{repo_id}/claude-md`)

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/{repo_id}/claude-md` | Preview the repowise-managed section of `CLAUDE.md` as JSON — no disk write. |
| `POST` | `/{repo_id}/claude-md/generate` | Regenerate and write `CLAUDE.md` to the repo root. |

---

## Webhooks (`/api/webhooks`)

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/webhooks/github` | GitHub push webhook receiver. Validates `X-Hub-Signature-256` against `REPOWISE_GITHUB_WEBHOOK_SECRET`. Returns `{"event_id": "...", "status": "accepted"}`. |
| `POST` | `/api/webhooks/gitlab` | GitLab push webhook receiver. Validates `X-Gitlab-Token` against `REPOWISE_GITLAB_WEBHOOK_TOKEN`. |

---

## Modules & Ownership

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/{repo_id}/modules` | List all modules with their page counts, health scores, and top files. |
| `GET` | `/{repo_id}/modules/{module_id}` | Single module detail with file list and wiki pages. |
| `GET` | `/{repo_id}/owners` | Paginated owner list with file/LOC breakdown. |
| `GET` | `/{repo_id}/owners/{owner}` | All files owned by a given author. |
| `GET` | `/{repo_id}/overview-summary` | Overview dashboard payload (architecture summary, module map, health ring, decision count). |
| `GET` | `/{repo_id}/knowledge-map` | Knowledge map: file → domain classification used for the wiki's module-level grouping. |

---

## Files & External Systems

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/{repo_id}/files` | File tree with graph metadata (PageRank, hotspot, symbol count). Query params: `path_prefix`, `sort`, `limit`. |
| `GET` | `/{repo_id}/files/{file_path}` | Single file node with full graph metadata, page links, co-changes, and ownership. |
| `GET` | `/{repo_id}/external-systems` | Detected external system dependencies (databases, APIs, queues). |
| `GET` | `/{repo_id}/external-systems/graph` | External system dependency graph in D3 format. |
| `GET` | `/{repo_id}/external-systems/calls` | Individual external-system call sites (file + line). |
| `GET` | `/{repo_id}/external-systems/{system_id}` | Single external system details. |

---

## Security Findings

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/{repo_id}/security` | Security findings from the most recent scan. Query params: `kind` (`secret`, `vulnerability`), `severity`, `status`. |

---

## Token Cost Tracking

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/{repo_id}/costs/summary` | Total and per-model token usage and estimated USD cost. |
| `GET` | `/{repo_id}/costs` | Paginated per-job cost records. |
| `GET` | `/{repo_id}/distill-savings` | Estimated cost savings from distill caching (tokens avoided vs. full-context baseline). |

---

## Feedback

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/feedback` | Submit anonymous product feedback. Body: `{"type": "...", "message": "..."}`. |

---

## Coupling Analysis

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/repos/{repo_id}/coupling` | Structural coupling metrics (afferent/efferent coupling, instability, abstractness) per module. |
