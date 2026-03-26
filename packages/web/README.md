# repowise Web UI

Next.js 15 frontend for the repowise codebase documentation engine. Provides an interactive interface for exploring AI-generated wiki pages, dependency graphs, code analytics, and architectural insights for any indexed repository.

## Tech Stack

| Layer | Technology |
|-------|------------|
| Framework | Next.js 15 (App Router), React 19, TypeScript 5.7 |
| Styling | Tailwind CSS v4 (CSS-first, no config file), custom design tokens in `globals.css` |
| Components | Radix UI primitives, class-variance-authority for variants |
| Data Fetching | SWR (stale-while-revalidate) with 30s polling, Server-Sent Events for live job progress |
| Visualization | D3.js (force-directed graphs), Recharts (bar/donut charts), Mermaid (diagrams) |
| Code Rendering | Shiki (syntax highlighting), next-mdx-remote (wiki content) |
| Search | cmdk command palette (Cmd+K), Fuse.js client-side fuzzy search |
| URL State | nuqs (type-safe URL search params for bookmarkable views) |
| Animation | Framer Motion |
| Icons | Lucide React |
| Fonts | Geist Sans + Geist Mono |

## Pages & Features

### Dashboard (`/`)

The home page. Shows aggregate stats across all registered repositories (total files, symbols, entry points, dead code findings), a list of repos with quick-action buttons, and recent indexing jobs with their status.

### Repository Overview (`/repos/[id]`)

Landing page for a single repo. Displays git stats (HEAD commit, branch, contributor count), documentation coverage at a glance, and quick links to all sub-pages. Includes an operations panel for triggering sync and full-resync jobs.

### Wiki Pages (`/repos/[id]/wiki/[...slug]`)

The core documentation viewer. Renders AI-generated wiki pages as MDX with:
- Shiki syntax-highlighted code blocks
- Embedded Mermaid diagrams (flowcharts, sequence diagrams, etc.)
- Auto-generated sticky table of contents from headings
- Confidence badge showing freshness (fresh/stale/outdated) with last-updated tooltip
- Git history panel showing the file's change timeline
- Regenerate button to force a fresh AI pass on stale pages

### Dependency Graph (`/repos/[id]/graph`)

Interactive D3 force-directed graph rendered on HTML Canvas. Supports six view modes:
- **Module view** — hierarchical module organization
- **Ego graph** — neighborhood of a selected node with context sidebar
- **Architecture view** — entry point reachability analysis
- **Dead code view** — highlights unreachable files
- **Hot files view** — commit activity heatmap overlay
- **Full graph** — complete dependency graph

Features pan/zoom, a minimap for orientation, a filter panel (by language, node type, edge type), node sizing controls, and a path finder to trace dependency chains between any two files.

### Search (`/repos/[id]/search`)

Full-text and semantic search across all wiki pages. Includes a type toggle (FTS / semantic / hybrid), debounced input, and result cards with highlighted snippets. Also powers the global command palette (Cmd+K) accessible from anywhere.

### Symbol Index (`/repos/[id]/symbols`)

Sortable, filterable table of all extracted symbols (functions, classes, constants, etc.). Filter by kind and language. Click any row to open a detail drawer with the symbol's signature, location, and relationships.

### Documentation Coverage (`/repos/[id]/coverage`)

Donut chart of overall documentation freshness plus a per-file table showing each page's confidence score, status (fresh/stale/outdated), and a per-row regenerate button for targeted updates.

### Code Ownership (`/repos/[id]/ownership`)

Contributor attribution view with granularity toggle (file/directory). Shows ownership percentages, knowledge silo badges (files owned by a single contributor), and a contributor activity chart.

### Hotspots (`/repos/[id]/hotspots`)

Ranked table of high-churn files with churn bar charts and an owner leaderboard. Helps identify files that change frequently and may need architectural attention.

### Dead Code (`/repos/[id]/dead-code`)

Tabbed view of dead code findings (unreachable files, unused exports). Supports row-level actions (ignore, mark as false positive), bulk selection, and a trigger to run fresh analysis. Summary bar shows counts by category.

### Architectural Decisions (`/repos/[id]/decisions`)

Lists extracted architectural decision records with health metrics. Each decision has a detail page (`/repos/[id]/decisions/[decisionId]`) showing rationale, status, and related code.

### Settings (`/settings` and `/repos/[id]/settings`)

Global settings page with sections for API connection, LLM provider/model selection, webhook configuration, and MCP integration. Per-repo settings available under each repository.

## Architecture

### Data Flow

```
Server Components (RSC)          Client Components
─────────────────────────        ──────────────────────
Fetch data via apiGet()    ──>   SWR hooks for polling/revalidation
Render static content            useSSE() for live job progress
Pass props to client             nuqs for URL state persistence
                                 useState for transient UI
```

Most pages are **React Server Components** that fetch data server-side. Client components are used only where interactivity is needed (D3 canvas, command palette, SSE streams, form state). The `"use client"` boundary is kept as narrow as possible.

### API Proxy

`next.config.ts` rewrites `/api/*` to `REPOWISE_API_URL/api/*`, so the frontend never hardcodes the backend URL and CORS is handled at the proxy layer.

### API Client (`src/lib/api/`)

Organized by domain — `repos.ts`, `pages.ts`, `graph.ts`, `search.ts`, `symbols.ts`, `jobs.ts`, `git.ts`, `dead-code.ts`, `decisions.ts`, `health.ts`. Each module exports typed async functions wrapping `apiGet`/`apiPost`/`apiPatch` from `client.ts`. Auth is handled via Bearer token from `localStorage` (browser) or `REPOWISE_API_KEY` env var (server).

### Custom Hooks (`src/lib/hooks/`)

| Hook | Purpose |
|------|---------|
| `useRepo`, `useRepos` | SWR wrappers for repository data with 30s refresh |
| `usePage`, `usePageVersions` | Page content and version history |
| `useSearch` | Debounced search (300ms, min 2 chars) |
| `useGraph` (+ variants) | Graph data with stable caching (no revalidate on focus) |
| `useSSE` | Generic SSE hook with reconnection, exponential backoff, named events |
| `useJob` | Combines SWR polling + SSE streaming for real-time job monitoring |
| `useDebounce` | Generic value debounce |

### Component Organization (`src/components/`)

```
components/
  ui/           Radix-based primitives (button, card, dialog, tabs, tooltip, etc.)
  layout/       Sidebar, mobile nav
  wiki/         Wiki renderer, code blocks, Mermaid, ToC, confidence badge
  graph/        D3 canvas, filter panel, minimap, tooltip, ego sidebar, path finder
  search/       Command palette, search bar, result cards
  jobs/         Generation progress (SSE), job logs
  repos/        Add repo dialog, operations panel, run config form
  coverage/     Coverage donut chart, freshness table
  dead-code/    Findings table, row actions, summary bar
  decisions/    Decisions table, detail view, health widget
  git/          Churn bars, contributor charts, hotspot/ownership tables
  shared/       Stat cards, empty states
```

### Design System

Dark-mode only. All visual tokens are CSS custom properties in `src/styles/globals.css` — surfaces, borders, text colors, accent blue (`#5B9CF6`), confidence colors (green/yellow/red), language colors for graph nodes, edge type colors, typography scale, spacing grid, radii, and z-index layers. Changing the design means editing one file.

## Development

```powershell
# From repo root — requires the repowise API server on port 7337
$env:REPOWISE_API_URL = "http://localhost:7337"
npm run dev --workspace packages/web
# Open http://localhost:3000
```

```powershell
# Type check
npm run type-check --workspace packages/web

# Lint
npm run lint --workspace packages/web
```
