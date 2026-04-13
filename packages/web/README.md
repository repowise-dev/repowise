# repowise Web UI

Next.js 15 frontend for the repowise codebase documentation engine. Provides an interactive interface for exploring AI-generated wiki pages, dependency graphs, code analytics, and architectural insights for any indexed repository.

## Tech Stack

| Layer | Technology |
|-------|------------|
| Framework | Next.js 15 (App Router), React 19, TypeScript 5.7 |
| Styling | Tailwind CSS v4 (CSS-first, no config file), custom design tokens in `globals.css` |
| Components | Radix UI primitives, class-variance-authority for variants |
| Data Fetching | SWR (stale-while-revalidate) with 30s polling, Server-Sent Events for live job progress |
| Graph Viz | @xyflow/react (React Flow) with ELK layout (elkjs) |
| Charts | Recharts (bar/donut charts), D3.js (force-directed minimap, treemap) |
| Diagrams | Mermaid v11 (embedded in wiki pages) |
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

Landing page for a single repo. Displays health score, git insights (commit activity, churn distribution, bus factor), documentation coverage, and quick links to all sub-pages. Includes a **Graph Intelligence** section with expandable architecture communities (cohesion scores, member lists, neighboring communities) and execution flows panel (entry point scoring, BFS call traces, cross-community classification). Operations panel for triggering sync and full-resync jobs.

### Wiki Pages (`/repos/[id]/wiki/[...slug]`)

The core documentation viewer. Renders AI-generated wiki pages as MDX with:
- Shiki syntax-highlighted code blocks
- Embedded Mermaid diagrams (flowcharts, sequence diagrams, etc.)
- Auto-generated sticky table of contents with anchor-linked headings (scroll-to-heading)
- Confidence badge showing freshness (fresh/stale/outdated) with last-updated tooltip
- Git history panel showing the file's change timeline
- Regenerate button to force a fresh AI pass on stale pages
- Graph intelligence sidebar (XL screens) with PageRank/betweenness percentiles, community label, degree counts, entry point badge
- Hallucination warnings — surfaces LLM validation findings (symbol references not found in source) as amber banners
- Version history with diff view — browse previous page versions and compare changes inline

### Dependency Graph (`/repos/[id]/graph`)

Interactive React Flow graph with ELK layout engine. Supports five view modes:
- **Module view** — hierarchical module organization with drill-down
- **Architecture view** — entry point reachability analysis
- **Dead code view** — highlights unreachable files with confidence groups
- **Hot files view** — commit activity heatmap overlay
- **Full graph** — complete dependency graph

Three color modes: **Language** (per-language coloring), **Community** (Leiden community clusters with real labels from analysis), **Risk** (churn-based).

Features pan/zoom, minimap, path finder for dependency chains, community detail panel (click any node in community mode to see members, cohesion, and neighboring communities with cross-edge counts), and URL param support for deep-linking color modes.

### Search (`/repos/[id]/search`)

Full-text and semantic search across all wiki pages. Includes a type toggle (FTS / semantic / hybrid), debounced input, and result cards with highlighted snippets. Also powers the global command palette (Cmd+K) accessible from anywhere.

### Symbol Index (`/repos/[id]/symbols`)

Sortable, filterable table of all extracted symbols (functions, classes, constants, etc.). Filter by kind and language. Click any row to open a two-panel detail drawer: left side shows the symbol's signature, docstring, and location; right side shows **graph intelligence** — PageRank/betweenness percentile badges, in/out degree, community membership, callers and callees with confidence scoring, and class heritage (extends/implements relationships).

### Documentation Coverage (`/repos/[id]/coverage`)

Donut chart of overall documentation freshness plus a per-file table showing each page's confidence score, status (fresh/stale/outdated), and a per-row regenerate button for targeted updates.

### Code Ownership (`/repos/[id]/ownership`)

Contributor attribution view with granularity toggle (file/directory). Shows ownership percentages, knowledge silo badges (files owned by a single contributor), and a contributor activity chart.

### Hotspots (`/repos/[id]/hotspots`)

Ranked table of high-churn files with churn bar charts and an owner leaderboard. Helps identify files that change frequently and may need architectural attention.

### Dead Code (`/repos/[id]/dead-code`)

Tabbed view of dead code findings (unreachable files, unused exports). Supports row-level actions (ignore, mark as false positive), bulk selection, and a trigger to run fresh analysis. Summary bar shows counts by category.

### Documentation Explorer (`/repos/[id]/docs`)

Split-pane documentation browser (VS Code-style). Left panel is a searchable file tree with type/freshness filters and colored freshness dots. Right panel renders full wiki content with:
- Mermaid diagram rendering and syntax-highlighted code blocks with copy buttons
- Graph intelligence sidebar (PageRank/betweenness percentiles, community, callers/callees)
- Version history browser with LCS-based inline diff view
- Hallucination warning banners for pages with detected inaccuracies
- Deep-linkable page selection via URL search params (`?page=...`)
- Export All (single `.md`) and Download ZIP buttons

### Architectural Decisions (`/repos/[id]/decisions`)

Lists extracted architectural decision records with health metrics. Each decision has a detail page (`/repos/[id]/decisions/[decisionId]`) showing context, rationale, alternatives, and consequences rendered as markdown, with Confirm/Dismiss/Deprecate actions.

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
| `useCommunities`, `useCommunityDetail` | Community summaries and detail data |
| `useGraphMetrics` | Per-node PageRank, betweenness, percentiles |
| `useCallersCallees` | Symbol call graph with confidence and heritage |
| `useExecutionFlows` | Entry point traces with community crossing |
| `useSSE` | Generic SSE hook with reconnection, exponential backoff, named events |
| `useJob` | Combines SWR polling + SSE streaming for real-time job monitoring |
| `useDebounce` | Generic value debounce |

### Component Organization (`src/components/`)

```
components/
  ui/           Radix-based primitives (button, card, dialog, tabs, tooltip, etc.)
  layout/       Sidebar, mobile nav
  wiki/         Wiki renderer (RSC + client), code blocks, Mermaid, ToC, confidence badge, version history with diff
  graph/        React Flow canvas, toolbar, legend, tooltip, community panel, path finder
  search/       Command palette, search bar, result cards
  jobs/         Generation progress (SSE), job logs
  repos/        Add repo dialog, operations panel, run config form
  coverage/     Coverage donut chart, freshness table
  dead-code/    Findings table, row actions, summary bar
  decisions/    Decisions table, detail view, health widget
  git/          Churn bars, contributor charts, hotspot/ownership tables
  symbols/      Symbol table, symbol drawer, symbol graph panel (metrics + callers)
  dashboard/    Health ring, attention panel, quick actions, language donut,
                ownership treemap, community summary grid, execution flows panel
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
