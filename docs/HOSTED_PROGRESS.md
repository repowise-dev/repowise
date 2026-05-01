# Hosted Implementation — Progress

Last updated: 2026-05-01 by session #1 (Phase 0 close-out)
Current phase: Phase 0 — Foundation & Hardening (effectively complete; awaiting PR + human-driven smoke + prod migration apply)
Current branch (frontend): `phase-0-foundation` (head 038e133, 7 commits ahead of main)
Current branch (backend):  `phase-0-foundation` (head b957dc0, 10 commits ahead of main)

## Supabase project IDs (per §2.5 rule 1)

| Role | Project ID | Notes |
|---|---|---|
| dev/prod (single) | `kdifhrxkaztlxkfozrib` | "repowise", ap-northeast-1, created 2026-04-03. Human confirmed 2026-05-01: this is the only Supabase project; treat it as the working target. Migrations land here directly via MCP. Standard caution applies (no `DROP/TRUNCATE/DELETE`-without-`WHERE` ad hoc; destructive changes go through reviewed migration files). |

## Phase status

| Phase | Status | Sessions | Branch | PR | Notes |
|---|---|---|---|---|---|
| 0  — Foundation & Hardening | ✅ Code done; awaiting PR | #1 | phase-0-foundation (both repos) | — | Test scaffolds, engine bump, migration 006, billing fixes, rate limiting, types/route cleanup, docs. |
| 1A — `@repowise/ui` extraction (types + ui scaffold) | ⬜ Not started | — | — | — | depends on 0 |
| 1B — Wire both apps + delete `components/demo/` | ⬜ Not started | — | — | — | depends on 1A |
| 2A — Blast radius + costs + knowledge map | ⬜ Not started | — | — | — | depends on 1B + 3A |
| 2B — Chat enhancements + wiki deep-link | ⬜ Not started | — | — | — | depends on 1B |
| 2C — Dashboard widgets + per-repo settings | ⬜ Not started | — | — | — | depends on 1B |
| 3A — pgvector + get_answer + semantic search | ⬜ Not started | — | — | — | depends on 0 |
| 3B — New chat tools + new endpoints | ⬜ Not started | — | — | — | depends on 3A |
| 3C — Engine v0.4.1 surface (heritage, communities, exec flows, co-changes) | ⬜ Not started | — | — | — | depends on 0 |
| 4A — Push webhook + polling auto-sync | ⬜ Not started | — | — | — | depends on 3 |
| 4B — Workspaces (data model, ingestion, API, UI) | ⬜ Not started | — | — | — | depends on 4A |
| 5  — Org/RBAC, SSO, SCIM, audit log, self-hosted, observability | ⬜ Not started | — | — | — | depends on 4 |
| 6  — Security scanning + compliance reports | ⬜ Not started | — | — | — | depends on 5 |
| 7  — Integrations (Jira, Linear, Slack, Confluence, Notion, Teams, PagerDuty/Opsgenie) | ⬜ Not started | — | — | — | depends on 5 |
| 8  — Website / marketing rollout | ⬜ Not started (rolling) | — | — | — | batched |

## Phase 0 — what shipped (session #1)

### Backend (`backend/`, branch `phase-0-foundation`)
- [x] `_cache` is already bounded (LRU + TTL — landed pre-Phase-0 in PR #1, merged into main before this session).
- [x] **Engine pin → `repowise==0.4.1`** in `modal_app/indexer.py` (commit 1e71ea7). Unblocks C# Full tier, framework-aware edges, Leiden communities, heritage extraction, dynamic-hint extractors, workspace contracts.
- [x] Resolved Dockerfile vs pyproject drift: `pyproject.toml` is now the single source of truth; Dockerfile installs from it via `pip install .` (commit 06cb88f). `modal>=1.0,<2`, `dodopayments>=1.93,<2` (matches the deployed Railway values).
- [x] **Migration 006 `atomic_credit_hold`** — defined Postgres function (single conditional UPDATE; NULL on insufficient); applied via Supabase MCP to `kdifhrxkaztlxkfozrib`; non-atomic TOCTOU fallback removed from `credit_service.check_and_hold_credits`. (commit bba58d2, migration file `migrations/006_atomic_credit_hold.sql`).
- [x] `_handle_subscription_renewed` now grants `pro_yearly_credit_cents` for yearly cycles (was always granting monthly — silently underpaying yearly subscribers). (commit a771cd1).
- [x] `_handle_subscription_on_hold` now downgrades tier to `free` (admins exempt) instead of just logging. (commit a771cd1).
- [x] **slowapi rate limits** wired in `app/main.py` + `app/rate_limit.py`: `/repos/index` 10/day, `/chat` 60/minute, `/repos/explore` 60/minute, `/repos/{owner}/{name}/latest` 60/minute. Token-suffix key for authed, IP for anon. (commit e3ccf55).
- [x] `.env.example` covering all env vars including `BACKEND_URL` (commit 6abaa68).
- [x] README updated for migrations 004–006, rate limits, `optional_user`, `BACKEND_URL`, `/billing/{models,estimate}`.
- [x] **Test scaffold**: `backend/tests/` with `conftest.py` (env bootstrap, FakeSupabase, async httpx client via ASGITransport, `auth_user(tier=...)` fixture), unit + integration directories, pytest-asyncio auto mode, `slow` marker. **14 tests passing.** (commit 9e4a71e + later additions).
- [x] CI workflow `.github/workflows/test-backend.yml` running pytest on push/PR (commit a0e9eef).
- [x] Backend `Makefile` (test/lint/type-check/ci) per guide §10.4 (commit b957dc0).
- [x] Expanded `.gitignore` to suppress `.claude/`, `.mcp.json`, `.repowise/`, `.venv/`, pytest/ruff caches (commit b19b0e1).

### Frontend (`frontend/`, branch `phase-0-foundation`)
- [x] `type-check`, `test`, `test:watch`, `ci` scripts added to `package.json`; `.claude/` + `.mcp.json` gitignored (commit e93fe13).
- [x] **Strengthened API types**: replaced `Record<string, unknown>` for `OverviewResponse.repo_structure` (→ `RepoStructure` with `PackageInfo[]`), `git_summary` (→ `GitSummary`), `GraphResponse.graph` (→ `CanonicalGraph`), `DeadCodeResponse.findings[]` (→ `DeadCodeFinding[]`), `DecisionsResponse.decisions[]` (→ `DecisionEntry[]`). Optional fields keep adapter compatibility for hosted-only enrichment. Backend schema changes will now surface as compile errors. (commit 336b05b).
- [x] **Orphan route** `/repos/[repoId]/decisions/[decisionId]/` moved to canonical `/s/[shortId]/decisions/[decisionId]/`; `/repos/` tree deleted; link in `decisions-table.tsx` updated (commit b1fada0).
- [x] `FreshnessTable` wired into `/s/[shortId]/coverage/page.tsx` (commit b1fada0).
- [x] **Vitest scaffold** (jsdom + Testing Library + jest-dom matchers); 4 tests passing (adapter unit tests + Skeleton component smoke). `vitest.config.ts` aliases `@/*` to match tsconfig. (commit 56b7a4e).
- [x] CI workflow `.github/workflows/test-frontend.yml` running typecheck + lint + vitest (commit 5a99851).
- [x] Frontend `Makefile` (commit 038e133).

### Repo-root
- [x] `docs/HOSTED_PROGRESS.md` created and populated (this file).

## Open items deferred / requires human

- **Smoke index of 6 fixture repos** (TS/JS, Python, Go, C++, Rust, multi-project .NET) against the new `repowise==0.4.1` Modal image — needs Modal credits, deploy of `modal_app/indexer.py`, and access to the dev Supabase storage. Recorded as test debt below.
- **Apply migration 006 to "prod"** — n/a in this setup (single project), already applied. If a separate prod project is later spun up, re-apply there with explicit human go-ahead per guide §2.5 rule 2.
- **Vercel preview deploy of `phase-0-foundation`** — needs branch push + Vercel's preview hook.
- **Railway deploy verification** — Dockerfile now installs from `pyproject.toml`; first build on phase-0 branch should be watched.
- **Dodo `subscription.on_hold` test scenario** in Dodo dashboard — verify a real test customer downgrades cleanly.
- **Live 429 rejection test** — slowapi is wired and asserted on app.state, but the limit is exercised only in unit-shape tests, not a real "fire 11 requests" path. Test debt.
- **Pre-existing untracked frontend work** (`src/components/landing/git-intelligence/GitIntelligenceAnimation.tsx`, `src/components/landing/graph-intelligence/`) is intentionally left untracked — out of Phase 0 scope. Human to commit on a separate branch when ready.

## Blockers

- (none — all Phase 0 hard blockers resolved this session)

## Decisions taken (with reason)

- 2026-05-01 — Single Supabase project (`kdifhrxkaztlxkfozrib`) used for both dev and prod for now, per human confirmation. Standard MCP write caution still applies.
- 2026-05-01 — `pyproject.toml` is the single source of truth for backend deps; Dockerfile no longer hand-lists them. Reason: prevents the modal/dodopayments major-version drift that was already present.
- 2026-05-01 — `modal>=1.0,<2` and `dodopayments>=1.93,<2` chosen by aligning to the values the Dockerfile had (which is what's actually deployed on Railway today). If `pip freeze` on the live container disagrees, retune in Phase 0 follow-up.
- 2026-05-01 — `_handle_subscription_on_hold` downgrades to `tier='free'` (no separate `subscription_status='on_hold'` column). Reason: minimum-scope fix; preserves `dodo_subscription_id` so a payment retry (`subscription.active`) flips them back to Pro automatically. A richer status/grace UI can come in Phase 2.
- 2026-05-01 — Rate limit policy: `/repos/index` 10/day, others 60/min flat across tiers. Per-tier rpm caps deferred (require post-auth key function) — recorded as Phase 1+ refinement.
- 2026-05-01 — Frontend orphan route moved (not deleted), preserving the decision-detail view at the canonical `/s/[shortId]/...` URL.
- 2026-05-01 — Vitest chosen over Jest for the frontend (modern, faster, native TS, plays well with Vite ecosystem). MSW deferred to first integration test that needs request mocking.

## Drift from plan

- 2026-05-01 — Plan §4 backend item 1 ("replace unbounded `_cache`") was already done in PR #1 before this session. No work needed — verified via inspection.
- 2026-05-01 — Plan said "delete the orphan decision route"; chose to **move** rather than delete so the detail view survives at the canonical URL. Equivalent outcome, slightly larger diff.

## Test debt

- 2026-05-01 — Smoke index of the 6 fixture repos against `repowise==0.4.1` not yet performed — needs Modal env. **High priority** before Phase 1 starts; the entire Phase 3 backend parity work assumes 0.4.1 artifact shapes are produced correctly.
- 2026-05-01 — Live 429 rejection test for slowapi — currently only configuration is asserted.
- 2026-05-01 — `app/services/artifact_service.py` shape readers are pass-through; consumer code in routers/dashboard/chat may need adapter shims for v0.4.x fields (`community_summary`, `temporal_hotspot_score`, `security_findings`, etc.). Per Phase 0 plan, defer to Phase 3 once smoke index confirms which fields actually appear.
- 2026-05-01 — Frontend has no integration / e2e tests yet; Vitest scaffold covers unit + component only. MSW + Playwright deferred until Phase 1B.
- 2026-05-01 — Backend coverage measurement not wired (no `pytest-cov` in deps). Add when targets in §4.4 start mattering.

## Migration log (per §2.5 rule 5)

| Timestamp | Project ID | Migration | Result |
|---|---|---|---|
| 2026-05-01 | kdifhrxkaztlxkfozrib | `006_atomic_credit_hold` (`public.atomic_credit_hold(uuid, integer)`) | applied via MCP; smoke-verified by calling with non-existent user (returned NULL as expected) |

## Next session entry point

**Phase 0 close-out tasks for the human (before Phase 1 kicks off):**
1. Push `phase-0-foundation` branch on **both** `backend/` and `frontend/` repos.
2. Open one PR per repo into `main`; squash-merge once CI is green.
3. Smoke-deploy the new Modal image: `modal deploy modal_app/indexer.py` from the merged main, then trigger an indexing run on a small public repo (e.g. a tiny TS sample) and confirm artifacts upload cleanly. Then repeat for one Python, one Go, one C++, one Rust, and one multi-project .NET repo. Record findings in this file's "Test debt" section.
4. Watch the first Railway deploy from `main` after merge (Dockerfile install path changed from hand-listed deps to `pip install .`).
5. Verify `subscription.on_hold` → free tier flow in the Dodo sandbox dashboard.

**Phase 1A entry point (next session):**
- Phase decision: pick **Option α** (`packages/hosted-web/` inside the OSS monorepo) per plan §5 recommendation, unless legal/AGPL surfaces a fresh concern.
- First files to extract: `packages/types/src/{graph,git,docs,decisions,dead-code,symbols,chat}.ts` — define the canonical types from the union of `packages/web/src/lib/api/types.ts` and `frontend/src/lib/api/types.ts` (the latter now strengthened in Phase 0, so the merge is much cheaper).
- See plan §5.3 for the day-by-day refactor sequence.

Last commit on `phase-0-foundation` (backend): `b957dc0` — "chore(backend): add Makefile with install/test/lint/ci targets"
Last commit on `phase-0-foundation` (frontend): `038e133` — "chore(frontend): add Makefile with install/test/lint/type-check/ci targets"

## Glossary / project-specific terms

- **Snapshot** — a single indexing run of a repo at a given commit + depth.
- **Workspace** — multi-repo grouping (Phase 4).
- **Org** — paying tenant (Phase 5). Distinct from Workspace.
- **Hosted backend** — `backend/` (FastAPI on Railway + Modal). Separate git repo.
- **Hosted frontend** — `frontend/` (Next.js 16 on Vercel). Separate git repo.
- **OSS engine / OSS web** — `packages/` and `packages/web/`. Third independent repo, out of scope here.
