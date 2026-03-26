# repowise Frontend — Build Status & Remaining Work

**Last updated:** 2026-03-20
**Current state:** Phase 3 complete. All polish, advanced features, responsive layout, loading skeletons, error boundaries, and a11y improvements are implemented.

---

## What's Already Done

| Area | Status |
|------|--------|
| Next.js 15 scaffold + Tailwind v4 design system | ✅ Complete |
| All API types (mirrors all 35 backend endpoints) | ✅ Complete |
| Base API client with auth injection | ✅ Complete |
| All data hooks (SWR, SSE, debounce, graph) | ✅ Complete |
| UI component library (Radix: Button, Badge, Card, Dialog, Select, Tabs, Tooltip, ScrollArea…) | ✅ Complete |
| Sidebar with collapsible repo navigation | ✅ Complete |
| Dashboard page (stat cards, repo list, recent jobs) | ✅ Complete |
| Repo overview page (git stats, wiki preview) | ✅ Complete |
| Wiki page structure (top bar, confidence badge, git panel, metadata) | ✅ Structure only |
| Wiki page content rendering | ✅ Complete (WikiRenderer, Shiki, Mermaid, TOC, git history panel) |
| Search page (debounce, type toggle, cmdk command palette) | ✅ Complete |
| Symbols page (sortable table, filter row, detail drawer, Shiki signatures) | ✅ Complete |
| Graph page (D3 canvas, zoom/pan, hover tooltips, filter panel, minimap) | ✅ Complete |
| Coverage page (donut chart, freshness table, per-file regenerate) | ✅ Complete |
| Ownership page (table, contributor chart, silo badges) | ✅ Complete |
| Hotspots page (ranked table, churn bars, owner leaderboard) | ✅ Complete |
| Dead Code page (tabs, row resolve/ack/dismiss, bulk select, analyze button) | ✅ Complete |
| Settings page (connection, provider, webhook, MCP) | ✅ Complete |
| Add repository dialog + sidebar button | ✅ Complete |
| Repo operations panel (sync / full resync + confirm) | ✅ Complete |
| Real-time job progress (SSE stream, log, elapsed, toasts) | ✅ Complete |
| MDX rendering (WikiRenderer, CodeBlock, MermaidDiagram, TOC) | ✅ Complete |
| Regenerate button on wiki page | ✅ Complete |
| Sonner toast notifications | ✅ Complete |

---

## Phase 1 — All Data Pages (COMPLETE)

**Goal:** Every stub page becomes a fully functional data view. Backend data is all available — this phase is pure frontend wiring and visualization.

**Status:** All 7 data pages and their components are fully built. See component file list below.

---

### 1.1 Search Page (`/repos/[id]/search`)

Full-featured search with type switching and instant results.

**API:** `GET /api/search?query=&search_type=(semantic|fulltext)&limit=`
**Returns:** `{ page_id, title, page_type, target_path, score, snippet, search_type }`

**UI to build:**
- Search input with debounce (already have `useSearch` hook — wire it in)
- Toggle: `Full-Text` | `Semantic` | `Hybrid` — updates `search_type` param
- Language filter dropdown (Python / TypeScript / Go / etc.)
- Result cards: file path, title, match snippet with highlight, score badge, link to wiki page
- Keyboard shortcut: `cmd+K` / `ctrl+K` — opens a `cmdk` command palette overlay from anywhere in the app (global, mounted in root layout)
- Empty state when no query / no results

**Component files:**
- `src/app/repos/[id]/search/page.tsx` — full implementation (client component)
- `src/components/search/search-bar.tsx` — reusable input + type toggle
- `src/components/search/search-result-card.tsx` — result row
- `src/components/search/command-palette.tsx` — `cmdk` global overlay

---

### 1.2 Symbols Page (`/repos/[id]/symbols`)

Searchable, filterable, sortable symbol index for a repo.

**API:** `GET /api/symbols?repo_id=&q=&kind=&language=&limit=&offset=`
**Returns:** `{ id, name, qualified_name, kind, file_path, start_line, signature, language, visibility, is_async, complexity_estimate }`

**UI to build:**
- Search input (substring search on symbol name, wired to `q` param)
- Filter row: `Kind` (function / class / method / interface / variable) and `Language` dropdowns
- Sortable table columns: Name, Kind, File, Language, Lines, Complexity
- Row click opens a drawer (Radix Dialog) showing:
  - Full signature (syntax-highlighted via Shiki)
  - Docstring if present
  - File path with line number link
  - `kind` and `visibility` badges
- Pagination — load more or page navigation (API supports `offset`)
- Summary bar: "X symbols across Y files"

**Component files:**
- `src/app/repos/[id]/symbols/page.tsx` — full implementation
- `src/components/symbols/symbol-table.tsx` — sortable table with filter row
- `src/components/symbols/symbol-drawer.tsx` — detail drawer

---

### 1.3 Hotspots Page (`/repos/[id]/hotspots`)

Ranked file list by churn + complexity. Shows where the most risky code lives.

**APIs used:**
- `GET /api/repos/{id}/hotspots?limit=` → `{ file_path, commit_count_90d, churn_percentile, primary_owner, is_hotspot }`
- `GET /api/repos/{id}/git-summary` → `{ total_files, hotspot_count, stable_count, average_churn_percentile, top_owners }`

**UI to build:**
- Summary bar: X hotspots / Y stable files / avg churn percentile
- Ranked table: Rank, File path, Commits (90d), Churn percentile (progress bar), Owner, Hotspot badge
- Churn percentile shown as a horizontal `Recharts` bar or colored progress bar (red = high churn)
- Top owners mini-leaderboard (from git-summary `top_owners`)
- Click file row → navigate to wiki page for that file (if exists)

**Component files:**
- `src/app/repos/[id]/hotspots/page.tsx` — full server component
- `src/components/git/hotspot-table.tsx`
- `src/components/git/churn-bar.tsx` — inline progress bar with color gradient

---

### 1.4 Ownership Page (`/repos/[id]/ownership`)

Code ownership breakdown — who owns what, silo detection.

**APIs used:**
- `GET /api/repos/{id}/ownership?granularity=module` → `{ module_path, primary_owner, owner_pct, file_count, is_silo }`
- `GET /api/repos/{id}/git-summary` → top_owners leaderboard

**UI to build:**
- Toggle: `Module` | `File` granularity (updates `granularity` query param)
- Ownership table: Module/File, Owner, Ownership%, File Count, Silo badge
  - Silo badge (ownership > 80%) highlighted in yellow with tooltip "Bus factor risk"
  - Ownership % shown as a colored progress bar
- Contributor sidebar: top 10 owners with file counts and percentage of total
- `Recharts` horizontal bar chart showing top 5 contributors by file count

**Component files:**
- `src/app/repos/[id]/ownership/page.tsx` — client component (toggle state)
- `src/components/git/ownership-table.tsx`
- `src/components/git/contributor-bar.tsx`

---

### 1.5 Coverage Page (`/repos/[id]/coverage`)

Documentation freshness breakdown — how much of the codebase is documented and up to date.

**APIs used:**
- `GET /api/pages?repo_id=&limit=500` → all pages with `freshness_status`, `confidence`, `target_path`, `page_type`
- `GET /api/symbols?repo_id=&limit=1` to get total symbol count (from API count header or paginated total)

**Derived metrics (computed client-side):**
- `fresh` = pages where `freshness_status === "fresh"`
- `stale` = `freshness_status === "stale"`
- `outdated` = `freshness_status === "outdated"`
- Coverage % = total_pages / estimated_file_count × 100

**UI to build:**
- Big coverage score at top (donut chart via Recharts or large text + confidence bar)
- Three stat cards: Fresh / Stale / Outdated with counts + percentage
- Freshness distribution bar (stacked horizontal bar: green/yellow/red)
- Table: File path, Status badge, Confidence %, Model used, Last generated
  - Filterable by status
- "Stale files" list with links to wiki pages + "Regenerate" button per row (calls `POST /api/pages/lookup/regenerate?page_id=`)

**Component files:**
- `src/app/repos/[id]/coverage/page.tsx` — server component with client sub-components
- `src/components/coverage/coverage-donut.tsx` — Recharts PieChart
- `src/components/coverage/freshness-table.tsx`

---

### 1.6 Dead Code Page (`/repos/[id]/dead-code`)

Unused files, exports, and zombie packages — triage and resolve workflow.

**APIs used:**
- `GET /api/repos/{id}/dead-code?min_confidence=0.4&status=open&limit=200`
- `GET /api/repos/{id}/dead-code/summary` → `{ total_findings, by_kind, deletable_lines, confidence_summary }`
- `POST /api/repos/{id}/dead-code/analyze` → trigger async analysis (202)
- `PATCH /api/dead-code/{finding_id}` → resolve/dismiss a finding (`status`: acknowledged | resolved | false_positive)

**UI to build:**
- Summary bar: total findings, deletable lines, breakdown by kind (unreachable_file / unused_export / unused_internal / zombie_package)
- Three tabs: **Unreachable** | **Unused Exports** | **Zombie Packages** (filters `kind`)
- Finding table per tab:
  - Columns: File, Symbol, Confidence, Owner, Lines, Safe-to-delete badge, Status
  - Row actions: Resolve / Acknowledge / Mark False Positive (calls `PATCH`)
  - Bulk select + bulk resolve
- "Analyze" button → calls `POST /analyze` → shows toast "Analysis started"
- Confidence filter slider (0.4 – 1.0)
- Safe-only toggle (filters `safe_to_delete=true`)

**Component files:**
- `src/app/repos/[id]/dead-code/page.tsx` — client component (tabs + row actions)
- `src/components/dead-code/findings-table.tsx`
- `src/components/dead-code/finding-row.tsx` — with inline action buttons
- `src/components/dead-code/summary-bar.tsx`

---

### 1.7 Dependency Graph Page (`/repos/[id]/graph`)

D3 force-directed graph of module dependencies.

**API:** `GET /api/graph/{repoId}` → `{ nodes: [{ node_id, node_type, language, symbol_count, pagerank, betweenness, community_id, is_test, is_entry_point }], links: [{ source, target, imported_names }] }`

**UI to build:**
- D3 force simulation on `<canvas>` (not SVG — 2000+ nodes need canvas performance)
  - Node size = `symbol_count` (capped at 3× base)
  - Node color = language (from `LANGUAGE_COLORS` in `confidence.ts`)
  - Edge color = `--color-edge-imports` / `--color-edge-calls` CSS vars
  - Entry point nodes have a ring indicator
  - Test nodes shown dimmed
- Zoom + pan (D3 zoom)
- Hover tooltip: node_id, language, symbol_count, pagerank, community_id
- Click node → navigate to wiki page for that file (if exists)
- Filter panel (right side):
  - Language multi-select
  - Hide test files toggle
  - Color-by: Language | Community | Entry Points
  - Size-by: Symbol Count | PageRank | Betweenness
- Node search (highlights matching nodes)
- Mini-map (small overview canvas in corner showing full graph, viewport rect)

**Component files:**
- `src/app/repos/[id]/graph/page.tsx` — thin server wrapper
- `src/components/graph/graph-canvas.tsx` — `"use client"` D3 canvas component
- `src/components/graph/graph-filter-panel.tsx` — filter controls
- `src/components/graph/graph-tooltip.tsx` — hover overlay
- `src/components/graph/graph-minimap.tsx` — overview canvas

---

## Phase 2 — CLI Operations & Full Control from UI (COMPLETE)

**Goal:** Every CLI command (`init`, `update`, `sync`, `dead-code`, `export`, `doctor`, `status`) is triggerable from the UI. All provider/model/API key config is manageable from the Settings page. Real-time job progress via SSE is fully surfaced.

**Estimated files:** ~20 new/modified files
**New dependencies needed:** `sonner`, `@radix-ui/react-progress`, `@radix-ui/react-switch`, `@radix-ui/react-slider`, `@radix-ui/react-label` — none are installed yet.

---

### 2.1 Settings Page (`/settings`)

Central config — everything needed to run repowise is configurable here.

**Sections:**

#### API Connection
- API server URL (reads/writes `NEXT_PUBLIC_REPOWISE_API_URL` via `.env.local` or just localStorage)
- API key (stored in `localStorage` as `repowise_api_key` — already read by `client.ts`)
- "Test connection" button → calls `GET /health` and shows server version + status

#### Default Provider & Model
- Provider dropdown: `litellm` | `openai` | `anthropic` | `ollama` | `mock`
- Model text input (e.g., `gemini/gemini-2.0-flash`, `gpt-4.1`, `claude-opus-4-6`)
- Saved to `localStorage` as `repowise_default_provider` and `repowise_default_model`
- Used as defaults when triggering init/sync from UI

#### Embedder Config
- Embedder dropdown: `mock` | `gemini`
- Embedding dimensions (default 768)
- Note: "Set REPOWISE_EMBEDDER env var when starting the server"
- Inline docs explaining when semantic search is active

#### Webhook Config (display only)
- GitHub webhook URL: `http://your-server:7337/api/webhooks/github`
- GitLab webhook URL: `http://your-server:7337/api/webhooks/gitlab`
- Secret env vars to set: `REPOWISE_GITHUB_WEBHOOK_SECRET`, `REPOWISE_GITLAB_WEBHOOK_TOKEN`
- Copy-to-clipboard buttons

#### MCP Config (display only)
- Auto-generated MCP config for Claude Code / Cursor / Cline
- Shows `repowise mcp {repo_path} --transport stdio` command
- Copy-to-clipboard

**Component files:**
- `src/app/settings/page.tsx` — full implementation
- `src/components/settings/connection-section.tsx`
- `src/components/settings/provider-section.tsx`
- `src/components/settings/webhook-section.tsx`
- `src/components/settings/mcp-section.tsx`
- `src/lib/config.ts` — localStorage read/write helpers for all settings

---

### 2.2 Add Repository Dialog

Register a new repo without using the CLI.

**API:** `POST /api/repos` with `{ name, local_path, url?, default_branch?, settings? }`

**UI:**
- "+ Add Repository" button in sidebar (below repo list) and on empty dashboard
- Radix Dialog with form:
  - `name` — text input
  - `local_path` — text input (e.g., `C:\Users\ragha\Desktop\interview-coach`)
  - `url` — optional git remote URL
  - `default_branch` — text input (default: `main`)
- Submit → `POST /api/repos` → mutate SWR cache → sidebar updates

**Component files:**
- `src/components/repos/add-repo-dialog.tsx` — Dialog with form
- Update `src/components/layout/sidebar.tsx` — add the "+ Add" button

---

### 2.3 Repo Operations Panel

Init, sync, and resync a repo from the UI — the most important CLI feature to expose.

This is a collapsible panel on the repo overview page (`/repos/[id]`), and also accessible from a "Run" button in the sidebar.

**Supported operations:**

| Operation | API | CLI Equivalent |
|-----------|-----|----------------|
| Incremental Sync | `POST /api/repos/{id}/sync` | `repowise update` |
| Full Resync | `POST /api/repos/{id}/full-resync` | `repowise init --force` |

**UI — "Run" panel:**
- Provider dropdown (defaults to saved settings provider)
- Model text input (defaults to saved settings model)
- Options: Skip Tests toggle, Skip Infra toggle, Concurrency slider (1–10)
- "Sync" button → `POST /api/repos/{id}/sync` with config
- "Full Resync" button (destructive — confirm dialog first) → `POST /api/repos/{id}/full-resync`
- After triggering: transition to Job Progress view (see 2.4)

**Component files:**
- `src/components/repos/operations-panel.tsx` — `"use client"` collapsible form
- `src/components/repos/run-config-form.tsx` — provider/model/options form (reused in 2.4)
- Update `src/app/repos/[id]/page.tsx` — embed operations panel

---

### 2.4 Real-Time Job Progress

Live SSE stream from `GET /api/jobs/{id}/stream`. Shows file-by-file generation progress.

**SSE Events:**
- `event: progress` → `{ job_id, status, completed_pages, total_pages, failed_pages, current_level }`
- `event: done` → same shape, triggers completion state
- `event: error` → `{ detail }` → show error state

**UI:**
- `GenerationProgress` component — mounted when a job is running
  - Progress bar: `completed_pages / total_pages`
  - Status text: "Generating level 2 — 47/142 pages"
  - Failed count badge (if > 0)
  - Elapsed time counter
  - Log lines: last 5 progress events shown as scrolling log
  - On `done`: show summary (total pages, tokens, elapsed, failed count)
  - On `error`: show error message with retry option
- Job list in dashboard auto-refreshes every 5s while any job is running
- Toast notification (sonner) on job completion/failure

**Component files:**
- `src/components/jobs/generation-progress.tsx` — `"use client"` SSE-driven component
- `src/components/jobs/job-log.tsx` — scrolling event log
- `src/lib/hooks/use-job.ts` — SWR hook for polling job status + SSE stream control
- Update `src/app/repos/[id]/page.tsx` — show `GenerationProgress` when job is running

---

### 2.5 Wiki Page MDX Rendering

Replace plain-text content display with full MDX rendering.

**Tools:** `next-mdx-remote` (server-side compilation), `shiki` (code highlighting), `mermaid` (diagrams)

**Components to build:**
- `src/components/wiki/wiki-renderer.tsx` — RSC that compiles MDX with `next-mdx-remote/rsc`
  - Custom MDX components: heading levels, code blocks, tables, blockquotes
- `src/components/wiki/code-block.tsx` — `"use client"` Shiki syntax highlighter
  - Language badge, copy button, line numbers, VSCode Dark+ theme
- `src/components/wiki/mermaid-diagram.tsx` — `"use client"` lazy Mermaid renderer
  - Dynamic import of `mermaid` to avoid SSR issues
- `src/components/wiki/table-of-contents.tsx` — extracted from MDX headings, sticky on scroll
- Update `src/app/repos/[id]/wiki/[...slug]/page.tsx` — swap plain text for `WikiRenderer`

---

### 2.6 Regenerate Button on Wiki Page

Allow regenerating a single page from the UI without running the full CLI.

**API:** `POST /api/pages/lookup/regenerate?page_id={page_id}` → `{ job_id, status }`

**UI:**
- "Regenerate" button in wiki page top bar (spinner while running)
- On click → POST regenerate → get `job_id` → open `GenerationProgress` in a Dialog
- On complete → revalidate page data (SWR mutate) → confidence badge updates

---

## Phase 3 — Polish, Advanced Features & Full Completeness (COMPLETE)

**Goal:** Production-grade UX. Command palette, advanced graph interactions, responsive layouts, loading states, error boundaries, accessibility, and any remaining gaps.

**Estimated files:** ~25 new/modified files

---

### 3.1 Global Command Palette (`cmd+K`)

Instant access to everything from anywhere in the app.

**Library:** `cmdk` (already installed)

**Features:**
- Sections: Recent Pages, Repos, Quick Actions, Symbols
- Quick Actions: "Init repo", "Open settings", "View jobs"
- Repo pages: navigate to any wiki page by typing its title or path
- Symbol lookup: type a symbol name → navigate to symbol drawer
- Keyboard: `↑/↓` navigate, `Enter` select, `Esc` close
- Mounted globally in `layout.tsx`

**Component files:**
- `src/components/search/command-palette.tsx` — full cmdk implementation
- Update `src/app/layout.tsx` — mount palette + register `cmd+K` listener

---

### 3.2 Advanced Graph Features

Completing the dependency graph from Phase 1 with path-finding and filtering.

**New features:**
- Shortest path finder: "From" + "To" node inputs → calls `GET /api/graph/{id}/path?from=&to=` → highlights path in orange on the graph canvas
- Community clustering: color nodes by `community_id` — nodes in same community use the same color family
- "Focus on file" mode: click a node → dim all non-adjacent nodes, show 1-hop neighborhood
- Export graph as PNG (canvas `.toDataURL()`)

**Component files:**
- `src/components/graph/path-finder.tsx` — UI controls for path query
- Update `src/components/graph/graph-canvas.tsx` — add path highlighting, community colors, focus mode

---

### 3.3 Git History Panel on Wiki Pages

Full per-file commit history surfaced in the wiki page context panel.

**API:** `GET /api/repos/{id}/git-metadata?file_path=` → `{ significant_commits, top_authors, co_change_partners, commit_count_90d, churn_percentile }`

**UI:**
- Recent commits list: SHA (7-char), message (truncated), author, relative date
- Author avatars (generated from author name initials)
- Co-change partners: "Often changed with: X, Y, Z" linked to their wiki pages
- Churn indicator: commit frequency sparkline (last 12 weeks, Recharts LineChart)

**Component files:**
- `src/components/wiki/git-history-panel.tsx` — full implementation
- `src/components/wiki/commit-row.tsx`
- `src/components/wiki/co-change-list.tsx`

---

### 3.4 Repo Settings & Danger Zone

Per-repo settings accessible from the repo overview.

**APIs:**
- `PATCH /api/repos/{id}` → update name, default_branch, settings
- (No delete endpoint in backend yet — add to Phase 3 wishlist)

**UI (tab on repo overview or separate `/repos/[id]/settings` route):**
- Repo name (editable)
- Default branch (editable)
- Local path (read-only display)
- Webhook URL display (repo-specific)
- Danger zone: "Full Resync" (red button, confirm dialog)

**Component files:**
- `src/app/repos/[id]/settings/page.tsx` — new route
- `src/components/repos/repo-settings-form.tsx`

---

### 3.5 Toast Notification System

Consistent feedback for all async operations.

**Library:** `sonner` (add to dependencies)

**Coverage:**
- ✅ Job started: "Sync started for {repo}"
- ✅ Job completed: "Documentation updated — 47 pages"
- ✅ Job failed: "{error message}" with retry button
- ✅ Repo added: "Repository registered"
- ✅ Finding resolved: "Marked as resolved"
- ✅ Page regenerated: "Page regeneration queued"

**Component files:**
- `src/components/ui/toaster.tsx` — Sonner Toaster wrapper
- Update `src/app/layout.tsx` — mount `<Toaster />`

---

### 3.6 Loading Skeletons for All Pages

Every data-fetching page needs `loading.tsx` with skeleton placeholders.

**Files to add:**
- `src/app/loading.tsx` — dashboard skeleton
- `src/app/repos/[id]/loading.tsx` — repo overview skeleton
- `src/app/repos/[id]/symbols/loading.tsx`
- `src/app/repos/[id]/hotspots/loading.tsx`
- `src/app/repos/[id]/ownership/loading.tsx`
- `src/app/repos/[id]/dead-code/loading.tsx`
- `src/app/repos/[id]/coverage/loading.tsx`
- `src/app/repos/[id]/graph/loading.tsx`
- `src/app/repos/[id]/search/loading.tsx`

Each skeleton matches the page's layout — no layout shift.

---

### 3.7 Error Boundaries

Graceful degradation when API calls fail.

**Files to add:**
- `src/app/error.tsx` — root error boundary
- `src/app/repos/[id]/error.tsx` — repo-level error (shows "Repo not found" + back button)
- `src/components/shared/api-error.tsx` — reusable error display with retry button

---

### 3.8 Responsive Layout

Full usability on tablet (768px) and mobile (375px).

**Changes:**
- Sidebar: collapses to icon rail on tablet, full-screen overlay on mobile
- Graph page: falls back to table view on mobile (canvas interaction impractical)
- Symbols/Hotspots/Ownership tables: horizontal scroll on small screens
- Dead code: card view instead of table on mobile

**Component files:**
- `src/components/layout/mobile-nav.tsx` — hamburger + sheet sidebar for mobile
- Update `src/components/layout/sidebar.tsx` — responsive breakpoints

---

### 3.9 Keyboard Navigation & Accessibility

Full WCAG 2.1 AA compliance.

**Audit & fix:**
- All interactive elements reachable by `Tab`
- Focus rings visible on all buttons/links/inputs
- `aria-label` on icon-only buttons
- `role="status"` on loading states
- `aria-live="polite"` on job progress updates
- Contrast ratio check on all text/background pairs

---

## Dependency Additions Needed

| Package | Phase | Purpose |
|---------|-------|---------|
| `sonner` | Phase 2 | Toast notifications |
| `@radix-ui/react-progress` | Phase 2 | Progress bar component |
| `@radix-ui/react-switch` | Phase 2 | Toggle switches in settings |
| `@radix-ui/react-slider` | Phase 2 | Concurrency slider |
| `@radix-ui/react-label` | Phase 2 | Form labels |

All other dependencies (D3, Recharts, cmdk, next-mdx-remote, shiki, framer-motion) are already installed.

---

## Build Order

The recommended build order within each phase:

**Phase 1** (DONE):
All 7 data pages complete in order: Hotspots → Ownership → Symbols → Dead Code → Coverage → Search → Graph.

**Phase 2** (each step unblocks the next):
1. `src/lib/config.ts` — settings localStorage helpers (used by everything)
2. Settings page — foundational, other pages reference saved config
3. Add Repository dialog — needed before operations panel makes sense
4. MDX rendering — high-value, independent
5. Operations panel (init/sync UI) — depends on config helpers for defaults
6. Job progress component — depends on operations panel triggering jobs
7. Regenerate button — depends on job progress component

**Phase 3** (independent, can be parallelized):
- Command palette, advanced graph, git history panel, repo settings, loading skeletons, error boundaries, responsive layout, a11y audit — all independent of each other.

---

## Page Completion Tracker

| Page | Phase 1 | Phase 2 | Phase 3 | Done |
|------|---------|---------|---------|------|
| `/` Dashboard | — | job progress polling | skeleton + error boundary | |
| `/repos/[id]` Overview | — | ✅ operations panel | repo settings tab | |
| `/repos/[id]/wiki/[...slug]` | — | ✅ MDX + regenerate + TOC | symbol hover | |
| `/repos/[id]/search` | ✅ full search UI | — | — | ✅ |
| `/repos/[id]/graph` | ✅ D3 canvas | — | path finder + community colors | ✅ (P1) |
| `/repos/[id]/symbols` | ✅ table + drawer | — | symbol hover card | ✅ (P1) |
| `/repos/[id]/coverage` | ✅ donut + table | — | skeleton | ✅ (P1) |
| `/repos/[id]/ownership` | ✅ table + chart | — | skeleton | ✅ (P1) |
| `/repos/[id]/hotspots` | ✅ table + churn bars | — | skeleton | ✅ (P1) |
| `/repos/[id]/dead-code` | ✅ tabs + resolve | — | skeleton | ✅ (P1) |
| `/settings` | — | ✅ full settings | — | ✅ |
| `/repos/[id]/settings` | — | — | ✅ repo settings | ✅ |
