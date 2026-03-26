# repowise — Frontend Build Prompt (Phase 8)

## Enterprise-Grade Web UI for a Codebase Documentation Engine

---

## Context

repowise is an open-source, self-hostable codebase documentation engine. The backend (Phases 1-7, 605 tests passing) generates structured, hierarchical wiki pages for any codebase using LLMs, keeps them in sync via webhooks/polling, and exposes everything through a REST API (30+ endpoints), MCP server (13 tools), and CLI (9 commands).

The frontend is the primary interface for enterprise customers. It must convey **engineering precision** and **editorial clarity** — the UI of a tool that senior engineers trust to understand their codebase.

**Target audience:** Engineering teams at mid-to-large companies (50-5000 engineers). Decision makers are Staff+ engineers and Engineering Managers. End users are IC developers navigating codebases they didn't write.

**Business context:** This will be offered as a hosted SaaS for enterprises. The UI is the product's face — it must look and feel like a $50K/year enterprise tool, not a side project.

---

## Stack Decision

### Recommended: Next.js 15 + Tailwind CSS v4 + shadcn/ui + D3.js

| Layer | Choice | Rationale |
|-------|--------|-----------|
| **Framework** | Next.js 15 (App Router) | SSR for wiki pages (SEO, fast first paint), RSC for data-heavy pages, streaming for SSE progress, API routes as BFF proxy |
| **Styling** | Tailwind CSS v4 | Utility-first, design token system via CSS variables, dark mode native, zero runtime cost |
| **Components** | shadcn/ui (Radix primitives) | Accessible, composable, copy-paste ownership (no version lock), enterprise-grade defaults |
| **Icons** | Lucide React | Consistent stroke weight, tree-shakeable, 1000+ icons, matches shadcn aesthetic |
| **MDX** | next-mdx-remote (server) | Server-side MDX compilation from DB content, custom component injection |
| **Syntax Highlighting** | Shiki (server) | Build-time highlighting, VSCode-quality themes, zero client JS for code blocks |
| **Diagrams** | Mermaid.js (client, lazy) | Renders Mermaid blocks from generated wiki content, lazy-loaded per viewport |
| **Graphs** | D3.js (d3-force, d3-hierarchy) | Force-directed dependency graphs, treemaps for ownership, full control over rendering |
| **Search** | nuqs (URL state) + Fuse.js (client fuzzy) | URL-synced search params, client-side fuzzy filtering for symbol index |
| **Animation** | Framer Motion | Spring physics, layout animations, shared element transitions, reduced-motion respect |
| **State** | React Server Components + SWR | RSC for initial data, SWR for client-side revalidation and SSE subscriptions |
| **Charts** | Recharts (simple) + D3 (complex) | Recharts for bar/line charts (coverage, tokens), D3 for graph/treemap/heatmap |
| **TypeScript** | Strict mode | Full type safety, API response types generated from backend Pydantic schemas |

### Package Updates Needed

The existing `packages/web/package.json` needs updates:

```json
{
  "dependencies": {
    "next": "15.1.0",
    "react": "^19.0.0",
    "react-dom": "^19.0.0",
    "d3": "^7.9.0",
    "d3-force": "^3.0.0",
    "d3-hierarchy": "^3.1.2",
    "mermaid": "^11.4.0",
    "shiki": "^1.22.0",
    "next-mdx-remote": "^5.0.0",
    "clsx": "^2.1.1",
    "tailwind-merge": "^2.5.4",
    "class-variance-authority": "^0.7.1",
    "lucide-react": "^0.460.0",
    "@radix-ui/react-dialog": "^1.1.0",
    "@radix-ui/react-dropdown-menu": "^2.1.0",
    "@radix-ui/react-tooltip": "^1.1.0",
    "@radix-ui/react-tabs": "^1.1.0",
    "@radix-ui/react-accordion": "^1.2.0",
    "@radix-ui/react-popover": "^1.1.0",
    "@radix-ui/react-scroll-area": "^1.2.0",
    "@radix-ui/react-select": "^2.1.0",
    "@radix-ui/react-separator": "^1.1.0",
    "@radix-ui/react-slot": "^1.1.0",
    "@radix-ui/react-toggle": "^1.1.0",
    "@radix-ui/react-toggle-group": "^1.1.0",
    "framer-motion": "^11.11.0",
    "nuqs": "^2.2.0",
    "swr": "^2.2.5",
    "recharts": "^2.13.0",
    "fuse.js": "^7.0.0",
    "cmdk": "^1.0.0"
  },
  "devDependencies": {
    "@types/d3": "^7.4.3",
    "@types/node": "^22",
    "@types/react": "^19",
    "@types/react-dom": "^19",
    "typescript": "^5.7",
    "eslint": "^9",
    "eslint-config-next": "15.1.0",
    "tailwindcss": "^4.0.0",
    "@tailwindcss/typography": "^0.5.15"
  }
}
```

---

## Design System

### Philosophy

**"Linear's exactness + Stripe's docs quality"**

The UI should feel like a precision instrument. Every pixel communicates information density without visual noise. Enterprise buyers judge software quality by its UI — repowise's frontend must signal "this team ships quality."

**Design references:**
- **Linear** — Navigation, command palette, dark theme, information density
- **Stripe Docs** — Content rendering, code blocks, sidebar navigation, typography
- **GitHub** — Dependency graphs, file trees, code navigation
- **Vercel** — Dashboard layout, deployment progress, clean dark UI
- **Raycast** — Command palette, search UX, keyboard-first

### Color System

**Dark mode primary.** No light mode in v1 — ship dark, add light later.

```css
/* Core Surfaces */
--bg-root: #0a0a0a;          /* Page background — NOT pure black (avoids OLED smear) */
--bg-surface: #111111;        /* Cards, panels */
--bg-elevated: #171717;       /* Hover states, active sidebar items */
--bg-overlay: #1a1a1a;        /* Modals, dropdowns, command palette */
--bg-inset: #080808;          /* Code blocks, inset areas */

/* Borders */
--border-default: rgba(255, 255, 255, 0.08);   /* Subtle structure */
--border-hover: rgba(255, 255, 255, 0.15);      /* Interactive elements */
--border-active: rgba(255, 255, 255, 0.25);     /* Focus rings */

/* Text */
--text-primary: #f0f0f0;      /* Headings, primary content */
--text-secondary: #a0a0a0;    /* Body text, descriptions */
--text-tertiary: #666666;     /* Timestamps, metadata, muted labels */
--text-inverse: #0a0a0a;      /* Text on accent backgrounds */

/* Accent — repowise Blue */
--accent-primary: #5B9CF6;     /* Links, active states, primary CTA */
--accent-hover: #7BB3F7;       /* Hover on accent elements */
--accent-muted: rgba(91, 156, 246, 0.15);  /* Accent backgrounds */

/* Confidence System — THE defining visual feature */
--confidence-fresh: #22c55e;   /* >= 0.80 — green badge */
--confidence-stale: #eab308;   /* 0.60 - 0.79 — yellow badge */
--confidence-outdated: #ef4444;/* < 0.60 — red badge */

/* Semantic */
--success: #22c55e;
--warning: #eab308;
--error: #ef4444;
--info: #5B9CF6;

/* Graph Node Colors (by language) */
--lang-python: #3776AB;
--lang-typescript: #3178C6;
--lang-go: #00ADD8;
--lang-rust: #DEA584;
--lang-java: #ED8B00;
--lang-cpp: #00599C;
--lang-config: #6B7280;
--lang-other: #8B5CF6;

/* Graph Edge Colors (by type) */
--edge-imports: #5B9CF6;
--edge-calls: #22c55e;
--edge-inherits: #a855f7;
--edge-implements: #ec4899;
--edge-co-change: #8b5cf6;   /* Dashed, purple */
```

### Typography

**Three-font system:** Geist Sans (prose) + Geist Mono (code/paths) + Inter (UI labels/numbers)

```css
/* Font Stack */
--font-sans: 'Geist', 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
--font-mono: 'Geist Mono', 'JetBrains Mono', 'Fira Code', monospace;
--font-ui: 'Inter', 'Geist', -apple-system, BlinkMacSystemFont, sans-serif;

/* Type Scale (rem-based, 16px root) */
--text-xs: 0.75rem;      /* 12px — timestamps, badges */
--text-sm: 0.8125rem;    /* 13px — metadata, sidebar items */
--text-base: 0.875rem;   /* 14px — body text, UI labels */
--text-lg: 1rem;         /* 16px — wiki prose, prominent UI text */
--text-xl: 1.25rem;      /* 20px — page titles */
--text-2xl: 1.5rem;      /* 24px — section headings */
--text-3xl: 2rem;        /* 32px — dashboard hero numbers */

/* Line Heights */
--leading-tight: 1.3;    /* Headings */
--leading-normal: 1.5;   /* UI text */
--leading-relaxed: 1.75; /* Wiki prose — optimized for long reading */

/* Monospace usage rules */
/* - File paths: always mono */
/* - Symbol names in backticks: always mono */
/* - Page slugs: always mono */
/* - Code blocks: always mono (Shiki handles this) */
/* - Numbers in data tables: tabular-nums (Inter) */
```

### Spacing & Layout

```css
/* 4px base grid */
--space-1: 0.25rem;   /* 4px */
--space-2: 0.5rem;    /* 8px */
--space-3: 0.75rem;   /* 12px */
--space-4: 1rem;      /* 16px */
--space-5: 1.25rem;   /* 20px */
--space-6: 1.5rem;    /* 24px */
--space-8: 2rem;      /* 32px */
--space-10: 2.5rem;   /* 40px */
--space-12: 3rem;     /* 48px */
--space-16: 4rem;     /* 64px */

/* Border Radius */
--radius-sm: 4px;     /* Badges, tags */
--radius-md: 6px;     /* Buttons, inputs */
--radius-lg: 8px;     /* Cards, panels */
--radius-xl: 12px;    /* Modals, large cards */

/* Z-Index Scale */
--z-base: 0;
--z-elevated: 10;      /* Sticky headers */
--z-dropdown: 20;      /* Dropdowns, popovers */
--z-sidebar: 30;       /* Mobile sidebar overlay */
--z-modal: 40;         /* Modals */
--z-command: 50;       /* Command palette */
--z-toast: 60;         /* Toast notifications */

/* Content widths */
--sidebar-width: 260px;
--context-panel-width: 280px;
--content-max-width: 768px;   /* Wiki prose reading width */
```

### Animation

```css
/* Easing */
--ease-out: cubic-bezier(0.16, 1, 0.3, 1);     /* Entering elements */
--ease-in: cubic-bezier(0.7, 0, 0.84, 0);       /* Exiting elements */
--ease-inout: cubic-bezier(0.87, 0, 0.13, 1);   /* Morphing */

/* Durations */
--duration-fast: 150ms;     /* Hover states, tooltips */
--duration-normal: 250ms;   /* Panel transitions, page transitions */
--duration-slow: 400ms;     /* Modal open/close, graph animations */

/* Rules:
   - Always respect prefers-reduced-motion
   - Exit animations 60-70% of enter duration
   - Never block user input during animation
   - Use transform/opacity only (never animate width/height)
*/
```

---

## Layout Architecture

### Three-Column Layout (Wiki Pages)

```
┌─────────────────────────────────────────────────────────────┐
│ Top Bar: breadcrumb · confidence badge · regenerate · model │
├──────────┬────────────────────────────┬─────────────────────┤
│          │                            │                     │
│ Sidebar  │     Wiki Content           │  Context Panel      │
│ 260px    │     (MDX rendered)         │  280px              │
│          │     max-width: 768px       │                     │
│ - Repo   │     centered               │  - Table of Contents│
│   tree   │                            │  - Referenced Symbols│
│ - Page   │                            │  - Dependency Mini  │
│   nav    │                            │    Map (D3, 1-hop)  │
│ - Search │                            │  - Git History Panel│
│          │                            │  - Page Metadata    │
│          │                            │                     │
├──────────┴────────────────────────────┴─────────────────────┤
│ Bottom: version history · last generated · token count      │
└─────────────────────────────────────────────────────────────┘
```

### Two-Column Layout (Dashboard, Data Pages)

```
┌────────────────────────────────────────────────────────────┐
│ Top Nav: repowise logo · repo selector · search · settings │
├──────────┬─────────────────────────────────────────────────┤
│          │                                                 │
│ Sidebar  │   Main Content Area                             │
│ 260px    │   (full width minus sidebar)                    │
│          │                                                 │
│ - Repos  │   Dashboard: stat cards + recent jobs           │
│ - Wiki   │   Graph: full-width D3 canvas                   │
│ - Graph  │   Search: results list + filters                │
│ - Search │   Symbols: sortable table                       │
│ - Symbols│   Coverage: progress bars + breakdown           │
│ - Coverage│  Ownership: treemap + contributor list         │
│ - Hotspots│  Hotspots: ranked list + bar charts           │
│ - Dead   │   Dead Code: three-tab table                    │
│   Code   │   Settings: form sections                       │
│ - Settings│                                                │
│          │                                                 │
└──────────┴─────────────────────────────────────────────────┘
```

### Responsive Behavior

| Breakpoint | Layout |
|------------|--------|
| >= 1440px | Three-column (sidebar + content + context) |
| 1024-1439px | Two-column (sidebar + content, context collapses to toggle panel) |
| 768-1023px | Content only (sidebar becomes slide-over, context becomes bottom drawer) |
| < 768px | Full-width content, bottom nav, accordion for panels |

---

## Pages — Detailed Specifications

### 1. Dashboard (`/`)

**Purpose:** Overview of all registered repos, sync health, recent activity.

**Data sources:**
- `GET /api/repos` — list all repos
- `GET /api/jobs?limit=10` — recent generation jobs
- `GET /health` — system health

**Layout:**
```
┌──────────────────────────────────────────────────────────┐
│ Stat Cards Row                                           │
│ ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────────┐ │
│ │ Total    │ │ Fresh    │ │ Stale    │ │ Dead Code    │ │
│ │ Pages    │ │ Pages    │ │ Pages    │ │ Findings     │ │
│ │   975    │ │   891    │ │    72    │ │    30        │ │
│ │ ■■■■■■■  │ │ ■■■■■■■  │ │ ■■       │ │ safe: 23     │ │
│ └──────────┘ └──────────┘ └──────────┘ └──────────────┘ │
├──────────────────────────────────────────────────────────┤
│ Repositories                                              │
│ ┌────────────────────────────────────────────────────────┐│
│ │ 🔵 repowise/backend  ·  Last sync: 2h ago  ·  Fresh  ││
│ │    847 pages  ·  23 modules  ·  94 symbols spotlighted ││
│ │    Provider: anthropic/claude-sonnet  ·  $4.20 total  ││
│ ├────────────────────────────────────────────────────────┤│
│ │ 🟡 acme/frontend     ·  Last sync: 3d ago  ·  Stale  ││
│ │    324 pages  ·  12 stale pages  ·  ~$1.80 total      ││
│ └────────────────────────────────────────────────────────┘│
├──────────────────────────────────────────────────────────┤
│ Recent Jobs                                               │
│ ┌────────────────────────────────────────────────────────┐│
│ │ incremental  ·  completed  ·  12 pages  ·  2m ago     ││
│ │ full_init    ·  running    ·  340/847   ·  SSE ●      ││
│ │ background   ·  completed  ·  5 pages   ·  8h ago     ││
│ └────────────────────────────────────────────────────────┘│
│                                                           │
│ Token Usage (30d)              Cost (30d)                 │
│ Input:  12.4M tokens           Estimated: $18.60          │
│ Output: 2.1M tokens            Cached:    $6.20 saved     │
└──────────────────────────────────────────────────────────┘
```

**Key interactions:**
- Click repo → navigate to `/repos/[id]`
- Click running job → SSE progress modal
- Stat cards link to filtered views (e.g., click "Stale" → `/repos/[id]/wiki?freshness=stale`)

---

### 2. Repo Overview (`/repos/[id]`)

**Purpose:** Landing page for a single repository. Shows the auto-generated overview wiki page alongside key metrics.

**Data sources:**
- `GET /api/repos/{id}`
- `GET /api/pages/lookup?page_id=repo_overview:{repo_name}`
- `GET /api/repos/{id}/git-summary`
- `GET /api/repos/{id}/dead-code/summary`

**Layout:**
- Left sidebar: file/module tree (collapsible, shows page freshness dots)
- Main: rendered repo overview wiki page (MDX)
- Key metrics strip above content: total pages, coverage %, freshness breakdown, hotspot count
- Architecture Mermaid diagram inline (from generated overview page)

---

### 3. Wiki Page (`/repos/[id]/wiki/[...slug]`)

**Purpose:** The core experience. Renders a single wiki page with rich context.

**Data sources:**
- `GET /api/pages/lookup?page_id={slug}`
- `GET /api/pages/lookup/versions?page_id={slug}`
- `GET /api/symbols?file_path={target_path}`
- `GET /api/graph?repo_id={id}` (filtered to 1-hop neighbors)
- `GET /api/repos/{id}/git-metadata?file_path={target_path}`

**MDX Rendering Pipeline:**

```
DB content_md → next-mdx-remote compile → inject custom components → render
```

**Custom MDX Components:**

| Component | Behavior |
|-----------|----------|
| `` `SymbolName` `` (backtick) | Auto-link: if matches a known symbol, render as hover card link |
| `<HoverCard>` | Shows: signature, file path, confidence badge, "Go to wiki page" link |
| ```` ```language ```` | Shiki server-side highlighting + "Copy" button + "View source" link (to GitHub/GitLab line) |
| ```` ```mermaid ```` | Client-side Mermaid.js rendering, lazy-loaded when in viewport |
| `[[page:file_page:src/auth.py]]` | Internal wiki link with inline confidence badge |

**Top Bar:**
- Breadcrumb: Repo > Module > File
- Confidence badge (clickable — shows decay history)
- "Regenerate" button (triggers `POST /api/pages/lookup/regenerate`)
- Provider badge: `anthropic / claude-sonnet-4-6`
- Source commit: short SHA linking to git commit

**Context Panel (right sidebar):**
1. **Table of Contents** — auto-generated from MDX headings, highlights current section on scroll
2. **Referenced Symbols** — list of symbols mentioned in this page, each a hover card link
3. **Dependency Mini-Map** — small D3 force-directed graph showing this file's 1-hop neighbors (imports/imported-by)
4. **Git History Panel** — primary owner, commit stats (total, 90d, 30d), is_hotspot flag, last 5 commits
5. **Co-change Partners** — files that frequently change with this one
6. **Dead Code Callout** — if any findings exist for this file, show amber banner: "2 unused exports detected" linking to dead code page

**Bottom Bar:**
- Version history dropdown (v3 — current, v2 — 3 days ago, v1 — initial)
- Click version → inline diff view (side-by-side or unified)
- "Generated at" timestamp + token count (input/output)

---

### 4. Search (`/repos/[id]/search`)

**Purpose:** Semantic and fulltext search across wiki content.

**Data sources:**
- `GET /api/search?query=...&search_type=semantic|fulltext&limit=20`

**Layout:**
- Search bar (prominent, cmd+K accessible from anywhere)
- Toggle: Semantic | Fulltext | Symbol
- Results grouped by page type (files, modules, symbols, overview)
- Each result: title, page type pill, relevance score bar, confidence badge, snippet with highlighted matches
- Click result → navigate to wiki page

**Command Palette (cmd+K):**
- Opens a cmdk dialog (like Linear/Raycast)
- Search across: pages, symbols, repos, actions (regenerate, sync, export)
- Recent pages section
- Keyboard navigation with arrow keys + enter

---

### 5. Dependency Graph (`/repos/[id]/graph`)

**Purpose:** Interactive visualization of the codebase dependency structure.

**Data sources:**
- `GET /api/graph?repo_id={id}`

**Implementation:** D3.js `d3-force` simulation.

**Node rendering:**
- Shape: circles
- Color: by language (use `--lang-*` tokens)
- Size: scaled by PageRank score (min 4px, max 24px radius)
- Label: file/module name (shown on hover or when zoomed in)
- Opacity: dead code nodes get 40% opacity + red dashed border
- Click: navigate to wiki page
- Hover: tooltip with file summary, confidence badge, PageRank rank

**Edge rendering:**
- Color: by edge type (imports=blue, calls=green, inherits=purple, implements=pink)
- Width: 1px default, 2px for strong connections (multiple edge types between same nodes)
- Co-change edges: dashed purple, width scaled by co_change_count
- Opacity: 0.3 default, 1.0 when connected to hovered node

**Filter Panel (left drawer or top bar):**
- Filter by: language, package, edge type, freshness status
- Color by: language | owner | churn | freshness
- Size by: PageRank | churn | complexity | LOC
- Toggle: co-change edges on/off
- Toggle: dead code highlighting on/off
- Search within graph (highlights matching nodes)

**Performance:**
- Canvas rendering for > 200 nodes (not SVG)
- WebGL via d3-force + PixiJS for > 1000 nodes
- Level-of-detail: cluster nodes at low zoom, expand at high zoom
- Debounce simulation ticks for smooth 60fps

---

### 6. Symbol Index (`/repos/[id]/symbols`)

**Purpose:** Searchable, sortable table of all extracted symbols.

**Data sources:**
- `GET /api/symbols?repo_id={id}&limit=50&offset=0`

**Columns:**
| Column | Sortable | Notes |
|--------|----------|-------|
| Name | Yes | Monospace, link to wiki page |
| Kind | Yes (filter) | function, class, method, interface, enum, etc. |
| File | Yes | Monospace path, truncated with tooltip |
| Language | Yes (filter) | Color dot + name |
| Visibility | Yes (filter) | public, private, protected |
| PageRank | Yes | Horizontal bar, ranked |
| Complexity | Yes | Numeric with color coding (green < 10, yellow 10-20, red > 20) |
| Async | Yes (filter) | Checkmark icon |
| Wiki | - | Link icon → wiki page |

**Features:**
- Client-side fuzzy search via Fuse.js (filter as you type)
- Column visibility toggle (hide less-used columns)
- Pagination with configurable page size (25, 50, 100)
- Export to CSV

---

### 7. Coverage (`/repos/[id]/coverage`)

**Purpose:** Documentation coverage metrics. Shows what % of the codebase has wiki pages and their freshness.

**Data sources:**
- `GET /api/repos/{id}` (total files, total pages, stale counts)
- `GET /api/pages?repo_id={id}&limit=0` (page counts by type)

**Layout:**
```
┌───────────────────────────────────────────────────────┐
│ Overall Coverage: 94.2%  ■■■■■■■■■■■■■■■■■■■░░       │
├───────────────────────────────────────────────────────┤
│ Freshness Breakdown                                   │
│ Fresh (>=0.80):    891 pages  ■■■■■■■■■■■■■■■■■       │
│ Stale (0.60-0.79):  72 pages  ■■                      │
│ Outdated (<0.60):   12 pages  ░                       │
├───────────────────────────────────────────────────────┤
│ By Page Type                                          │
│ File pages:        847 / 903 files (93.8%)            │
│ Module pages:       23 / 23 modules (100%)            │
│ Symbol spotlights:  94 / top 10% symbols              │
│ API contracts:       3 / 3 (100%)                     │
│ Config/infra:        7 / 9 (77.8%)                    │
│ Repo overview:       1 / 1 (100%)                     │
├───────────────────────────────────────────────────────┤
│ Undocumented Files (click to expand)                  │
│ src/generated/proto_pb2.py (skipped: generated)       │
│ tests/conftest.py (skipped: test file)                │
└───────────────────────────────────────────────────────┘
```

---

### 8. Ownership (`/repos/[id]/ownership`)

**Purpose:** Treemap visualization of code ownership. Identifies knowledge silos.

**Data sources:**
- `GET /api/repos/{id}/ownership?granularity=file`

**Layout:**
- Main: D3 treemap (cells = files, area = LOC, color = primary owner)
- Each contributor gets a unique color (auto-assigned from a colorblind-safe categorical palette)
- Cells with `owner_pct > 0.8`: amber highlight border (knowledge silo warning)
- Click cell → navigate to wiki page
- Hover cell → tooltip: file path, primary owner, owner %, contributor count, last commit

**Right sidebar:**
- Contributor list: each with color swatch, total % ownership, file count, last commit date
- Filter by contributor (click to highlight their files)
- "Knowledge Silos" section: modules owned by one person
- "Abandoned" section: modules with no commits in 90+ days

---

### 9. Hotspots (`/repos/[id]/hotspots`)

**Purpose:** Rank files by risk (high churn + high complexity).

**Data sources:**
- `GET /api/repos/{id}/hotspots?limit=30`

**Layout:**
- Ranked list, each row:
  ```
  1. src/core/ingestion/parser.py
     Churn: ████████████░░░░ (87th percentile)   Complexity: ████████░░░░ (62)
     Owner: alice@co  ·  90d commits: 34  ·  Contributors: 5  ·  Hotspot
     → View wiki page
  ```
- Filter by: package, owner, minimum churn percentile
- "Why is this a hotspot?" expandable tooltip explaining the scoring
- Link each row to the file's wiki page

---

### 10. Dead Code (`/repos/[id]/dead-code`)

**Purpose:** Report of dead/unused code findings with actionable resolution.

**Data sources:**
- `GET /api/repos/{id}/dead-code`
- `GET /api/repos/{id}/dead-code/summary`

**Layout:**

**Summary header:**
```
7 unreachable files  ·  23 unused exports  ·  ~2,847 deletable lines
Safe to delete: 23 findings  ·  Estimated savings: 2,100 LOC
```

**Three tabs:** Files | Exports | Internals (matching `kind` filter)

**Table columns:**
| Column | Notes |
|--------|-------|
| File/Symbol | Monospace, truncated path + symbol name |
| Package | Module/directory grouping |
| Confidence | Dots visualization (0.0-1.0) |
| Lines | Deletable line count |
| Last Touched | Relative time ("3 months ago") |
| Owner | Primary owner email |
| Safe | Green checkmark if safe_to_delete |
| Actions | "Resolve" button (marks as acknowledged), "View" link to wiki page |

**Sort by:** confidence (default), lines, last touched, owner

---

### 11. Settings (`/settings`)

**Purpose:** Configure repowise instance.

**Sections:**
1. **Provider** — current provider, model, API key status (masked)
2. **Generation** — concurrent workers, cascade budget, temperature
3. **Sync** — polling interval, webhook URLs (display only), auto-regen threshold
4. **Git** — co-change commit limit, blame enabled, depth auto-upgrade
5. **Dead Code** — enabled, min confidence, safe threshold, dynamic patterns
6. **API** — API key management, CORS origins
7. **About** — version, license, links to docs/GitHub

---

## Key Components — Implementation Notes

### `<WikiRenderer />`

The core MDX rendering component.

```tsx
// Simplified architecture
async function WikiRenderer({ content_md, symbols, repoId }: Props) {
  // 1. Server-side: compile MDX with next-mdx-remote
  const mdxSource = await compileMDX(content_md, {
    components: {
      // Auto-link backtick symbols
      code: ({ children }) => <SymbolAutoLink text={children} symbols={symbols} />,
      // Shiki-highlighted code blocks (server-rendered)
      pre: ({ children }) => <ShikiCodeBlock>{children}</ShikiCodeBlock>,
      // Mermaid blocks → client component (lazy)
      MermaidDiagram: dynamic(() => import('./MermaidDiagram'), { ssr: false }),
    }
  });

  return (
    <article className="prose prose-invert max-w-none">
      {mdxSource}
    </article>
  );
}
```

### `<SymbolHoverCard />`

Appears on hover over auto-linked symbol names.

```
┌──────────────────────────────────┐
│ AuthService (class)        Fresh │
│ src/auth/service.py:24           │
├──────────────────────────────────┤
│ class AuthService:               │
│   def authenticate(              │
│     self, token: str             │
│   ) -> User | None               │
├──────────────────────────────────┤
│ Imported by 12 files             │
│ PageRank: #3                     │
│ → Go to wiki page                │
└──────────────────────────────────┘
```

### `<ConfidenceBadge />`

The confidence score badge — appears everywhere.

```tsx
// Three states with distinct visual treatment
function ConfidenceBadge({ score, status }: { score: number; status: string }) {
  // Fresh: solid green dot + "Fresh" text
  // Stale: pulsing yellow dot + "Stale" text + "since 3d ago"
  // Outdated: solid red dot + "Outdated" text + warning icon
}
```

### `<DependencyMiniMap />`

Small D3 force-directed graph in the wiki page context panel.

- Shows this file + its 1-hop neighbors (imports and imported-by)
- Current file highlighted with accent ring
- Neighbor nodes colored by language
- Edges colored by type
- Click neighbor → navigate to its wiki page
- Max 20 nodes displayed (top by PageRank if more)

### `<GenerationProgress />`

SSE-connected component for live generation tracking.

```tsx
function GenerationProgress({ jobId }: { jobId: string }) {
  // Connect to GET /api/jobs/{jobId}/stream (text/event-stream)
  // Display: progress bar, pages done/total, current page name,
  //          tokens used, estimated cost, estimated time remaining
  // On "done" event: auto-refresh page data
  // On "error" event: show error with retry button
}
```

### `<CommandPalette />`

Global search and command execution (cmd+K).

Built with `cmdk` library.

```
┌───────────────────────────────────────┐
│ 🔍 Search pages, symbols, actions...  │
├───────────────────────────────────────┤
│ Recent                                │
│   AuthService · file_page · Fresh     │
│   packages/core · module_page         │
├───────────────────────────────────────┤
│ Pages                                 │
│   parser.py · file_page · Fresh       │
│   ingestion/ · module_page · Stale    │
├───────────────────────────────────────┤
│ Actions                               │
│   Sync repository                     │
│   Export wiki as markdown             │
│   Open settings                       │
└───────────────────────────────────────┘
```

---

## API Integration Layer

### API Client Architecture

```
packages/web/
├── src/
│   ├── lib/
│   │   ├── api/
│   │   │   ├── client.ts        # Base fetch wrapper with auth, error handling
│   │   │   ├── types.ts         # TypeScript types matching backend Pydantic schemas
│   │   │   ├── repos.ts         # GET/POST /api/repos
│   │   │   ├── pages.ts         # GET /api/pages, /api/pages/lookup
│   │   │   ├── search.ts        # GET /api/search
│   │   │   ├── jobs.ts          # GET /api/jobs, SSE stream helper
│   │   │   ├── symbols.ts       # GET /api/symbols
│   │   │   ├── graph.ts         # GET /api/graph
│   │   │   ├── git.ts           # GET /api/repos/{id}/git-*, hotspots, ownership
│   │   │   ├── dead-code.ts     # GET /api/repos/{id}/dead-code
│   │   │   └── health.ts        # GET /health
│   │   ├── hooks/
│   │   │   ├── use-repo.ts      # SWR hook for repo data
│   │   │   ├── use-page.ts      # SWR hook for wiki page
│   │   │   ├── use-search.ts    # SWR hook with debounced query
│   │   │   ├── use-sse.ts       # Generic SSE subscription hook
│   │   │   └── use-graph.ts     # SWR hook for graph data with transform
│   │   └── utils/
│   │       ├── cn.ts            # clsx + tailwind-merge
│   │       ├── format.ts        # Number, date, token formatters
│   │       └── confidence.ts    # Score → status → color helpers
```

### SSE Helper

```tsx
function useSSE<T>(url: string, options?: { enabled?: boolean }) {
  // 1. Create EventSource connection when enabled
  // 2. Parse "event: progress" → update state
  // 3. Parse "event: done" → mark complete, close connection
  // 4. Parse "event: error" → surface error, close connection
  // 5. Auto-reconnect on disconnect (3 retries, exponential backoff)
  // 6. Cleanup on unmount
}
```

### Auth

```tsx
// API calls include Authorization header when REPOWISE_API_KEY is set
// The key is stored in an httpOnly cookie (set by the settings page)
// Or passed via environment variable NEXT_PUBLIC_REPOWISE_API_KEY for simple setups
```

---

## File Structure

```
packages/web/
├── src/
│   ├── app/
│   │   ├── layout.tsx                     # Root layout: fonts, theme, sidebar
│   │   ├── page.tsx                       # Dashboard (/)
│   │   ├── repos/
│   │   │   └── [id]/
│   │   │       ├── layout.tsx             # Repo layout: sidebar nav
│   │   │       ├── page.tsx               # Repo overview (/repos/[id])
│   │   │       ├── wiki/
│   │   │       │   └── [...slug]/
│   │   │       │       └── page.tsx       # Wiki page (/repos/[id]/wiki/*)
│   │   │       ├── search/
│   │   │       │   └── page.tsx           # Search (/repos/[id]/search)
│   │   │       ├── graph/
│   │   │       │   └── page.tsx           # Dependency graph
│   │   │       ├── symbols/
│   │   │       │   └── page.tsx           # Symbol index
│   │   │       ├── coverage/
│   │   │       │   └── page.tsx           # Coverage report
│   │   │       ├── ownership/
│   │   │       │   └── page.tsx           # Ownership treemap
│   │   │       ├── hotspots/
│   │   │       │   └── page.tsx           # Hotspot list
│   │   │       └── dead-code/
│   │   │           └── page.tsx           # Dead code report
│   │   └── settings/
│   │       └── page.tsx                   # Settings
│   ├── components/
│   │   ├── ui/                            # shadcn/ui primitives
│   │   │   ├── button.tsx
│   │   │   ├── badge.tsx
│   │   │   ├── card.tsx
│   │   │   ├── table.tsx
│   │   │   ├── tabs.tsx
│   │   │   ├── dialog.tsx
│   │   │   ├── dropdown-menu.tsx
│   │   │   ├── tooltip.tsx
│   │   │   ├── popover.tsx
│   │   │   ├── scroll-area.tsx
│   │   │   ├── select.tsx
│   │   │   ├── separator.tsx
│   │   │   ├── input.tsx
│   │   │   ├── skeleton.tsx
│   │   │   └── command.tsx                # cmdk wrapper
│   │   ├── layout/
│   │   │   ├── sidebar.tsx                # Global sidebar
│   │   │   ├── top-bar.tsx                # Page top bar
│   │   │   ├── context-panel.tsx          # Right sidebar (wiki pages)
│   │   │   └── mobile-nav.tsx             # Mobile navigation
│   │   ├── wiki/
│   │   │   ├── wiki-renderer.tsx          # MDX rendering pipeline
│   │   │   ├── symbol-hover-card.tsx      # Symbol hover card
│   │   │   ├── symbol-auto-link.tsx       # Auto-link backtick symbols
│   │   │   ├── mermaid-diagram.tsx        # Client-side Mermaid (lazy)
│   │   │   ├── shiki-code-block.tsx       # Server-side Shiki
│   │   │   ├── confidence-badge.tsx       # Confidence score badge
│   │   │   ├── provider-badge.tsx         # Model/provider attribution
│   │   │   ├── page-version-diff.tsx      # Inline version diff
│   │   │   └── toc.tsx                    # Table of contents
│   │   ├── graph/
│   │   │   ├── dependency-graph.tsx        # Full-page D3 force graph
│   │   │   ├── dependency-mini-map.tsx     # Small sidebar D3 graph
│   │   │   ├── graph-controls.tsx          # Filter panel
│   │   │   └── graph-tooltip.tsx           # Node hover tooltip
│   │   ├── data/
│   │   │   ├── dead-code-table.tsx         # Dead code findings table
│   │   │   ├── ownership-treemap.tsx       # D3 treemap
│   │   │   ├── hotspot-list.tsx            # Ranked hotspot list
│   │   │   ├── git-history-panel.tsx       # Git metadata panel
│   │   │   └── generation-progress.tsx     # SSE progress component
│   │   └── shared/
│   │       ├── command-palette.tsx          # Global cmd+K
│   │       ├── file-tree.tsx               # Collapsible file tree
│   │       ├── search-bar.tsx              # Reusable search input
│   │       ├── stat-card.tsx               # Dashboard stat card
│   │       ├── empty-state.tsx             # Empty state with action
│   │       └── error-boundary.tsx          # Error fallback UI
│   ├── lib/
│   │   ├── api/                            # API client (see above)
│   │   ├── hooks/                          # React hooks (see above)
│   │   └── utils/                          # Utility functions
│   └── styles/
│       └── globals.css                     # Tailwind directives + CSS variables
├── public/
│   └── fonts/                              # Self-hosted Geist + Geist Mono
├── next.config.ts
├── tailwind.config.ts
├── tsconfig.json
└── package.json
```

---

## Build Order (Phase 8 Steps)

Follow this sequence. Each step should be testable in isolation.

```
Step 1: Scaffold + Design System (Foundation)
  1.1  Next.js 15 scaffold with App Router
  1.2  Tailwind v4 config with full design token system (CSS variables)
  1.3  Font loading: Geist, Geist Mono (self-hosted)
  1.4  shadcn/ui setup + core primitives (button, card, badge, dialog, etc.)
  1.5  Root layout with sidebar skeleton + dark theme
  1.6  API client base + types matching backend Pydantic schemas
  → Test: app starts, shows empty shell with correct fonts/colors

Step 2: Dashboard + Repo Overview
  2.1  Dashboard page: stat cards, repo list, recent jobs
  2.2  API hooks: use-repos, use-jobs
  2.3  Repo overview page: overview wiki page + metrics strip
  2.4  File tree sidebar component
  → Test: dashboard shows mock data, navigate to repo overview

Step 3: Wiki Page Rendering (CORE)
  3.1  WikiRenderer: MDX compilation with next-mdx-remote
  3.2  Shiki code blocks (server-side, dark theme)
  3.3  Mermaid diagram rendering (client-side, lazy)
  3.4  SymbolAutoLink + SymbolHoverCard
  3.5  ConfidenceBadge component
  3.6  Table of Contents (auto-generated from headings)
  3.7  Context panel: TOC + symbols + metadata
  3.8  Top bar: breadcrumb + confidence + regenerate + provider badge
  3.9  Page version diff viewer
  → Test: render a sample wiki page with all features visible

Step 4: Search
  4.1  Command palette (cmd+K) with cmdk
  4.2  Dedicated search page with type toggle
  4.3  Search result cards with highlighting
  → Test: search returns and displays results

Step 5: Dependency Graph
  5.1  D3 force-directed graph (Canvas renderer)
  5.2  Node/edge rendering with colors and sizing
  5.3  Filter panel (language, edge type, color-by, size-by)
  5.4  Hover tooltips + click navigation
  5.5  DependencyMiniMap (sidebar version)
  → Test: graph renders, nodes clickable, filters work

Step 6: Data Pages
  6.1  Symbol index: sortable table + fuzzy search
  6.2  Coverage page: progress bars + breakdown
  6.3  Ownership page: D3 treemap + contributor sidebar
  6.4  Hotspots page: ranked list with bar charts
  6.5  Dead code page: three-tab table + summary
  → Test: all data pages render with correct data

Step 7: Real-time + Polish
  7.1  GenerationProgress SSE component
  7.2  GitHistoryPanel in context panel
  7.3  Settings page
  7.4  Responsive layouts (tablet, mobile)
  7.5  Loading skeletons for all pages
  7.6  Error boundaries + empty states
  7.7  Keyboard navigation audit
  7.8  Accessibility audit (contrast, focus rings, aria labels)
  → Test: full app walkthrough, all pages, all interactions
```

---

## Quality Standards

### Performance Targets

| Metric | Target |
|--------|--------|
| LCP (Largest Contentful Paint) | < 1.5s |
| FID (First Input Delay) | < 100ms |
| CLS (Cumulative Layout Shift) | < 0.1 |
| Wiki page render (after data) | < 200ms |
| Graph render (500 nodes) | < 2s initial, 60fps after |
| Search results appear | < 300ms |

### Accessibility

- WCAG 2.1 AA minimum across all pages
- Color contrast: 4.5:1 for normal text, 3:1 for large text
- Focus rings visible on all interactive elements
- `prefers-reduced-motion` respected (disable animations, reduce transitions)
- Screen reader: meaningful `aria-label` on all icon buttons
- Keyboard: full navigation without mouse
- Skip-to-content link

### Code Quality

- TypeScript strict mode, no `any`
- All API responses typed (generate from backend Pydantic schemas)
- React Server Components by default, `"use client"` only when needed
- Component co-location: tests alongside components
- Storybook for complex components (wiki renderer, graph, treemap)

---

## Enterprise Considerations

These are important for the hosted SaaS offering:

1. **Multi-tenancy ready:** API client supports organization-level routing
2. **SSO integration:** Login page ready for SAML/OIDC (Phase 9+)
3. **RBAC hooks:** UI can conditionally render based on user roles (viewer, editor, admin)
4. **Audit trail:** All "regenerate" and "resolve" actions logged with user context
5. **White-labeling:** CSS variables make theming trivial (custom brand colors per org)
6. **Export:** Wiki export as PDF/HTML for compliance teams
7. **Embedding:** Wiki pages embeddable via iframe with `?embed=true` query param
8. **Telemetry:** Analytics events for page views, searches, graph interactions (opt-in)

---

## What This Document Is NOT

This document is the **frontend build prompt** — a comprehensive specification for building the repowise web UI. It is NOT:

- A design mockup (no Figma). The spec is precise enough to build from directly.
- A backend spec. The backend is complete (Phases 1-7). All API endpoints are documented in PLAN.md and tested.
- A deployment guide. Docker/CI is Phase 9.

**Next step:** Build Step 1 (scaffold + design system) and iterate from there.
