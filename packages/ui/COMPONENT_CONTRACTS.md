# @repowise/ui — Component Contracts

This document is the public-facing prop contract for components in
`@repowise/ui`. Each section lists the prop interface, with types
sourced from `@repowise/types` where the prop carries an engine
artifact. Components are presentational: they accept canonical data
via props, manage only UI-local state internally, and emit user
intent via callbacks. Data fetching, mutation, and routing live in
the consumer.

The contract is the package's public API. Breaking changes here
should be deliberate and called out in PR titles.

---

## `ui/*` — Radix-CVA primitives

Standard shadcn-style primitives wrapping Radix UI: `Badge`, `Button`,
`Card` (+ `CardHeader`/`CardTitle`/`CardDescription`/`CardContent`/
`CardFooter`), `ConfirmDialog`, `Dialog` (+ all `Dialog*` parts),
`Input`, `Label`, `Progress`, `ScrollArea`, `Select` (+ `Select*`
parts), `Separator`, `Sheet` (+ `Sheet*` parts), `Skeleton`, `Slider`,
`Switch`, `Tabs` (+ `Tabs*` parts), `Tooltip` (+ `Tooltip*` parts).

Props mirror the underlying Radix primitive plus a `className` prop
merged via `cn` (`tailwind-merge` + `clsx`). `Button` and `Badge`
expose CVA `variant` and `size` unions documented inline at the
component definition.

---

## `shared/api-error` — `ApiError`

Renders an inline error card with retry affordance.

| Prop | Type | Required | Notes |
|------|------|----------|-------|
| `error` | `Error \| null \| undefined` | yes | Surfaced in the card; `null` / `undefined` renders nothing. |
| `onRetry` | `() => void` | no | When provided, a "Retry" button is rendered. |
| `className` | `string` | no | Forwarded to the outer container. |

---

## `shared/empty-state` — `EmptyState`

Empty-list placeholder with optional icon and CTA.

| Prop | Type | Required | Notes |
|------|------|----------|-------|
| `title` | `string` | yes | Heading text. |
| `description` | `string` | no | Sub-line; shown below the title. |
| `icon` | `React.ReactNode` | no | Rendered above the title. |
| `action` | `{ label: string; onClick: () => void }` | no | Renders a primary `Button` when supplied. |
| `className` | `string` | no | Forwarded to the outer container. |

---

## `shared/stat-card` — `StatCard`

Single-stat display tile.

| Prop | Type | Required | Notes |
|------|------|----------|-------|
| `label` | `string` | yes | Uppercase label above the value. |
| `value` | `string \| number` | yes | Rendered tabular-nums. |
| `description` | `string` | no | Caption under the value. |
| `trend` | `{ value: string; positive: boolean }` | no | Renders an up/down arrow next to the value. |
| `icon` | `React.ReactNode` | no | Right-aligned glyph. |
| `className` | `string` | no | Forwarded to the outer Card. |
| `href` | `string` | no | Reserved for a follow-up routing prop; currently inert. |

---

## `coverage/coverage-donut` — `CoverageDonut`

Recharts donut visualising fresh/stale/outdated counts with a centred
percentage label for the fresh slice.

| Prop | Type | Required | Notes |
|------|------|----------|-------|
| `fresh` | `number` | yes | Count of fresh pages. |
| `stale` | `number` | yes | Count of stale pages. |
| `outdated` | `number` | yes | Count of outdated pages. |

Behaviour: zero-value slices are filtered before render; if all three
are zero the donut renders empty and the centre text shows `0%`.

---

## `decisions/decision-health-widget` — `DecisionHealthWidget`

Three-card summary tile for active / proposed / stale counts.

| Prop | Type | Required | Notes |
|------|------|----------|-------|
| `health` | `DecisionHealth \| undefined` (`@repowise/types/decisions`) | yes | Renders nothing while undefined; useful for the "loading or absent" state. |

---

## `decisions/decisions-table` — `DecisionsTable`

Filterable table of `DecisionRecord` rows with status + source dropdowns.

| Prop | Type | Required | Notes |
|------|------|----------|-------|
| `decisions` | `DecisionRecord[] \| undefined` (`@repowise/types/decisions`) | yes | Filtered list as resolved by the caller. |
| `filters` | `DecisionsTableFilters` | yes | Controlled. Caller mirrors these into fetch keys so filter changes drive a re-fetch. |
| `onFiltersChange` | `(filters: DecisionsTableFilters) => void` | yes | Fires on every dropdown change. |
| `repoId` | `string` | yes | Used to build the `/repos/{repoId}/decisions/{id}` link target for each row. |
| `error` | `unknown` | no | Truthy on a failed fetch; renders an inline retry message. |
| `isLoading` | `boolean` | no | Suppresses the "no decisions found" empty-state during the first fetch. |
| `onRetry` | `() => void` | no | Wired to the inline retry button when `error` is truthy. |

Filter unions:
- `DecisionStatusFilter` = `DecisionStatus \| "all"`
- `DecisionSourceFilter` = `DecisionSource \| "all"`

---

## `dead-code/summary-bar` — `SummaryBar`

Four-tile summary header for a dead-code report: total findings,
deletable lines, breakdown by kind, breakdown by confidence band.

| Prop | Type | Required | Notes |
|------|------|----------|-------|
| `summary` | `DeadCodeSummary` (`@repowise/types/dead-code`) | yes | Caller fetches the rollup. |

---

## `docs/docs-tree` — `DocsTree`

Filterable, collapsible directory tree of `DocPage` entries. Special
pages (`repo_overview`, `architecture_diagram`) appear at the top
level; module pages become directories with their page attached;
file pages nest under their parent directory.

| Prop | Type | Required | Notes |
|------|------|----------|-------|
| `pages` | `DocPage[]` (`@repowise/types/docs`) | yes | Caller fetches; tree builds the directory hierarchy each render. |
| `selectedPageId` | `string \| null` | yes | Currently-active page id; the matching node is highlighted. |
| `onSelectPage` | `(page: DocPage) => void` | yes | Fires on leaf-page or module-page click. |
| `className` | `string` | no | Forwarded to the outer container. |

Behaviour: built-in search, type filter (`all` / `file_page` /
`module_page` / `symbol_spotlight` / `repo_overview`), and freshness
filter (`all` / `fresh` / `stale` / `outdated`). Filter state is
UI-local — there's no fetch coupling.

---

## `coverage/freshness-table` — `FreshnessTable`

Filterable table of `DocPage` rows with per-row regenerate action.

| Prop | Type | Required | Notes |
|------|------|----------|-------|
| `pages` | `DocPage[]` (`@repowise/types/docs`) | yes | Caller fetches the list. The table does not mutate. |
| `onRegenerate` | `(pageId: string) => Promise<void>` | no | Caller wires to a mutation API. The table tracks per-row pending state internally and disables the row's button while in flight. Omit to hide the regenerate column. |

Behaviour:

- Filter tabs (`All`, `Fresh`, `Stale`, `Outdated`) are UI-local state;
  the canonical `freshness_status` field on `DocPage` is the filter
  predicate.
- Confidence column is colour-graded against the same thresholds as
  `lib/confidence.scoreToStatus`.
- Empty state (`EmptyState`) appears when the filtered set is empty.

---

## `git/*` — Hotspot, ownership, and contributor visualisations

All twelve components are presentational and accept canonical engine
artifacts via props (`Hotspot`, `OwnershipEntry` from `@repowise/types/git`,
or anonymous shapes for partner / owner / category records). None reach
out to data hooks or mutation APIs — fetching belongs to the caller.

### `git/churn-bar` — `ChurnBar`

Inline horizontal bar shaded green/yellow/red against thresholds at
50% / 75% percentile.

| Prop | Type | Required |
|------|------|----------|
| `percentile` | `number` (0–100, clamped) | yes |
| `className` | `string` | no |

### `git/co-change-list` — `CoChangeList`

Top-5 co-change partners with relative-magnitude bars.

| Prop | Type | Required |
|------|------|----------|
| `partners` | `Array<{ file_path: string; co_change_count: number }>` | yes |

### `git/churn-histogram` — `ChurnHistogram`

Recharts bar chart binning hotspots into 10-percentile buckets.

| Prop | Type | Required |
|------|------|----------|
| `hotspots` | `Hotspot[]` (`@repowise/types/git`) | yes |

### `git/commit-category-donut` — `CommitCategoryDonut`

Recharts donut with centred dominant-category label. Returns `null`
when the total is zero.

| Prop | Type | Required |
|------|------|----------|
| `categories` | `Record<string, number>` | yes |

### `git/commit-category-sparkline` — `CommitCategorySparkline`

Single-row stacked bar sized by category counts; each segment wears a
tooltip. Returns `null` when the total is zero.

| Prop | Type | Required |
|------|------|----------|
| `categories` | `Record<string, number>` | yes |

### `git/contributor-bar` — `ContributorBar`

Top-5 horizontal bar of files-by-owner.

| Prop | Type | Required |
|------|------|----------|
| `owners` | `Array<{ name; email?; file_count; pct }>` | yes |

### `git/contributor-network` — `ContributorNetwork`

Force-directed (`d3-force`) co-ownership graph. Computes nodes/links
from `hotspots[].primary_owner` and shared-file overlap; top-20
contributors only.

| Prop | Type | Required |
|------|------|----------|
| `hotspots` | `Hotspot[]` (`@repowise/types/git`) | yes |

### `git/hotspot-table` — `HotspotTable`

Searchable, filterable, sortable table of hotspots. Filter chips:
`all` / `hot` / `risk` / `accelerating`. Sortable columns:
`commits` / `churn` / `trend`. UI-local state — no fetch coupling.

| Prop | Type | Required |
|------|------|----------|
| `hotspots` | `Hotspot[]` (`@repowise/types/git`) | yes |

### `git/ownership-table` — `OwnershipTable`

Search + silo filter over module ownership rows.

| Prop | Type | Required |
|------|------|----------|
| `entries` | `OwnershipEntry[]` (`@repowise/types/git`) | yes |

### `git/ownership-treemap` — `OwnershipTreemap`

Treemap (`d3-hierarchy`) with file-count area, owner-hash colour, and
silo highlight stroke. Hover tooltips computed against the container's
bounding rect.

| Prop | Type | Required |
|------|------|----------|
| `entries` | `OwnershipEntry[]` (`@repowise/types/git`) | yes |

### `git/risk-distribution-chart` — `RiskDistributionChart`

Top-30 risk-scored vertical bar chart with average reference line.
Score = `0.4·churn + 0.35·busFactor + 0.25·trend`, all normalised to
0–100.

| Prop | Type | Required |
|------|------|----------|
| `hotspots` | `Hotspot[]` (`@repowise/types/git`) | yes |

### `git/bus-factor-panel` — `BusFactorPanel`

Stacked bar of safe / warning / risk counts plus a top-5 highest-risk
files list.

| Prop | Type | Required |
|------|------|----------|
| `hotspots` | `Hotspot[]` (`@repowise/types/git`) | yes |

---

## `jobs/job-log` — `JobLog`

Auto-scrolling fixed-height log viewer for streaming job progress
entries.

| Prop | Type | Required | Notes |
|------|------|----------|-------|
| `entries` | `Array<{ text: string; level?: number }>` | yes | Caller accumulates from SSE / poll. |
| `maxLines` | `number` | no | Tail length; default `6`. |

---

## `wiki/code-block` — `CodeBlock`

Server-rendered fenced code block. The caller passes pre-highlighted
HTML (e.g. from Shiki) plus the raw code for copy-to-clipboard.

| Prop | Type | Required | Notes |
|------|------|----------|-------|
| `code` | `string` | yes | Raw text used for the copy action. |
| `language` | `string` | no | Shown in the header bar. |
| `html` | `string` | yes | Trusted HTML, set via `dangerouslySetInnerHTML`. |

## `wiki/confidence-badge` — `ConfidenceBadge`

Coloured pill showing freshness status (`fresh` / `stale` / `outdated`).
Computes status from `score` unless `status` is supplied. When stale
with `staleSince`, wraps in a tooltip exposing the date and confidence.

| Prop | Type | Required |
|------|------|----------|
| `score` | `number` (0–1) | yes |
| `status` | `string` | no |
| `showScore` | `boolean` | no |
| `staleSince` | `string \| null` | no |
| `className` | `string` | no |

## `wiki/git-history-panel` — `GitHistoryPanel`

Sidebar panel summarising file lifecycle, commit categories, top
authors with bars, co-change partners, and recent commits.

| Prop | Type | Required |
|------|------|----------|
| `git` | `GitMetadata` (`@repowise/types/git`) | yes |

## `wiki/mermaid-diagram` — `MermaidDiagram`

Client-side mermaid renderer with dynamic import to avoid SSR. Uses a
dark theme matched to the design tokens. Failures render an inline
error card.

| Prop | Type | Required |
|------|------|----------|
| `chart` | `string` | yes |

## `wiki/table-of-contents` — `TableOfContents`

Extracts `#`/`##`/`###` headings from a markdown string and renders an
anchor list. An `IntersectionObserver` highlights the active heading.
Returns `null` when fewer than two headings are found.

| Prop | Type | Required |
|------|------|----------|
| `content` | `string` | yes |

## `wiki/wiki-markdown` — `WikiMarkdown`

Client-side markdown renderer (`react-markdown` + `remark-gfm`) with
slugged heading anchors, copy-on-hover code blocks, and inline
`MermaidDiagram` for `\`\`\`mermaid` fences.

| Prop | Type | Required |
|------|------|----------|
| `content` | `string` | yes |
