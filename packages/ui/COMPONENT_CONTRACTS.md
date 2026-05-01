# @repowise-dev/ui â€” Component Contracts

This document is the public-facing prop contract for components in
`@repowise-dev/ui`. Each section lists the prop interface, with types
sourced from `@repowise-dev/types` where the prop carries an engine
artifact. Components are presentational: they accept canonical data
via props, manage only UI-local state internally, and emit user
intent via callbacks. Data fetching, mutation, and routing live in
the consumer.

The contract is the package's public API. Breaking changes here
should be deliberate and called out in PR titles.

---

## `ui/*` â€” Radix-CVA primitives

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

## `shared/api-error` â€” `ApiError`

Renders an inline error card with retry affordance.

| Prop | Type | Required | Notes |
|------|------|----------|-------|
| `error` | `Error \| null \| undefined` | yes | Surfaced in the card; `null` / `undefined` renders nothing. |
| `onRetry` | `() => void` | no | When provided, a "Retry" button is rendered. |
| `className` | `string` | no | Forwarded to the outer container. |

---

## `shared/empty-state` â€” `EmptyState`

Empty-list placeholder with optional icon and CTA.

| Prop | Type | Required | Notes |
|------|------|----------|-------|
| `title` | `string` | yes | Heading text. |
| `description` | `string` | no | Sub-line; shown below the title. |
| `icon` | `React.ReactNode` | no | Rendered above the title. |
| `action` | `{ label: string; onClick: () => void }` | no | Renders a primary `Button` when supplied. |
| `className` | `string` | no | Forwarded to the outer container. |

---

## `shared/stat-card` â€” `StatCard`

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

## `coverage/coverage-donut` â€” `CoverageDonut`

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

## `decisions/decision-health-widget` â€” `DecisionHealthWidget`

Three-card summary tile for active / proposed / stale counts.

| Prop | Type | Required | Notes |
|------|------|----------|-------|
| `health` | `DecisionHealth \| undefined` (`@repowise-dev/types/decisions`) | yes | Renders nothing while undefined; useful for the "loading or absent" state. |

---

## `decisions/decisions-table` â€” `DecisionsTable`

Filterable table of `DecisionRecord` rows with status + source dropdowns.

| Prop | Type | Required | Notes |
|------|------|----------|-------|
| `decisions` | `DecisionRecord[] \| undefined` (`@repowise-dev/types/decisions`) | yes | Filtered list as resolved by the caller. |
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

## `dead-code/summary-bar` â€” `SummaryBar`

Four-tile summary header for a dead-code report: total findings,
deletable lines, breakdown by kind, breakdown by confidence band.

| Prop | Type | Required | Notes |
|------|------|----------|-------|
| `summary` | `DeadCodeSummary` (`@repowise-dev/types/dead-code`) | yes | Caller fetches the rollup. |

---

## `docs/docs-tree` â€” `DocsTree`

Filterable, collapsible directory tree of `DocPage` entries. Special
pages (`repo_overview`, `architecture_diagram`) appear at the top
level; module pages become directories with their page attached;
file pages nest under their parent directory.

| Prop | Type | Required | Notes |
|------|------|----------|-------|
| `pages` | `DocPage[]` (`@repowise-dev/types/docs`) | yes | Caller fetches; tree builds the directory hierarchy each render. |
| `selectedPageId` | `string \| null` | yes | Currently-active page id; the matching node is highlighted. |
| `onSelectPage` | `(page: DocPage) => void` | yes | Fires on leaf-page or module-page click. |
| `className` | `string` | no | Forwarded to the outer container. |

Behaviour: built-in search, type filter (`all` / `file_page` /
`module_page` / `symbol_spotlight` / `repo_overview`), and freshness
filter (`all` / `fresh` / `stale` / `outdated`). Filter state is
UI-local â€” there's no fetch coupling.

---

## `coverage/freshness-table` â€” `FreshnessTable`

Filterable table of `DocPage` rows with per-row regenerate action.

| Prop | Type | Required | Notes |
|------|------|----------|-------|
| `pages` | `DocPage[]` (`@repowise-dev/types/docs`) | yes | Caller fetches the list. The table does not mutate. |
| `onRegenerate` | `(pageId: string) => Promise<void>` | no | Caller wires to a mutation API. The table tracks per-row pending state internally and disables the row's button while in flight. Omit to hide the regenerate column. |

Behaviour:

- Filter tabs (`All`, `Fresh`, `Stale`, `Outdated`) are UI-local state;
  the canonical `freshness_status` field on `DocPage` is the filter
  predicate.
- Confidence column is colour-graded against the same thresholds as
  `lib/confidence.scoreToStatus`.
- Empty state (`EmptyState`) appears when the filtered set is empty.

---

## `git/*` â€” Hotspot, ownership, and contributor visualisations

All twelve components are presentational and accept canonical engine
artifacts via props (`Hotspot`, `OwnershipEntry` from `@repowise-dev/types/git`,
or anonymous shapes for partner / owner / category records). None reach
out to data hooks or mutation APIs â€” fetching belongs to the caller.

### `git/churn-bar` â€” `ChurnBar`

Inline horizontal bar shaded green/yellow/red against thresholds at
50% / 75% percentile.

| Prop | Type | Required |
|------|------|----------|
| `percentile` | `number` (0â€“100, clamped) | yes |
| `className` | `string` | no |

### `git/co-change-list` â€” `CoChangeList`

Top-5 co-change partners with relative-magnitude bars.

| Prop | Type | Required |
|------|------|----------|
| `partners` | `Array<{ file_path: string; co_change_count: number }>` | yes |

### `git/churn-histogram` â€” `ChurnHistogram`

Recharts bar chart binning hotspots into 10-percentile buckets.

| Prop | Type | Required |
|------|------|----------|
| `hotspots` | `Hotspot[]` (`@repowise-dev/types/git`) | yes |

### `git/commit-category-donut` â€” `CommitCategoryDonut`

Recharts donut with centred dominant-category label. Returns `null`
when the total is zero.

| Prop | Type | Required |
|------|------|----------|
| `categories` | `Record<string, number>` | yes |

### `git/commit-category-sparkline` â€” `CommitCategorySparkline`

Single-row stacked bar sized by category counts; each segment wears a
tooltip. Returns `null` when the total is zero.

| Prop | Type | Required |
|------|------|----------|
| `categories` | `Record<string, number>` | yes |

### `git/contributor-bar` â€” `ContributorBar`

Top-5 horizontal bar of files-by-owner.

| Prop | Type | Required |
|------|------|----------|
| `owners` | `Array<{ name; email?; file_count; pct }>` | yes |

### `git/contributor-network` â€” `ContributorNetwork`

Force-directed (`d3-force`) co-ownership graph. Computes nodes/links
from `hotspots[].primary_owner` and shared-file overlap; top-20
contributors only.

| Prop | Type | Required |
|------|------|----------|
| `hotspots` | `Hotspot[]` (`@repowise-dev/types/git`) | yes |

### `git/hotspot-table` â€” `HotspotTable`

Searchable, filterable, sortable table of hotspots. Filter chips:
`all` / `hot` / `risk` / `accelerating`. Sortable columns:
`commits` / `churn` / `trend`. UI-local state â€” no fetch coupling.

| Prop | Type | Required |
|------|------|----------|
| `hotspots` | `Hotspot[]` (`@repowise-dev/types/git`) | yes |

### `git/ownership-table` â€” `OwnershipTable`

Search + silo filter over module ownership rows.

| Prop | Type | Required |
|------|------|----------|
| `entries` | `OwnershipEntry[]` (`@repowise-dev/types/git`) | yes |

### `git/ownership-treemap` â€” `OwnershipTreemap`

Treemap (`d3-hierarchy`) with file-count area, owner-hash colour, and
silo highlight stroke. Hover tooltips computed against the container's
bounding rect.

| Prop | Type | Required |
|------|------|----------|
| `entries` | `OwnershipEntry[]` (`@repowise-dev/types/git`) | yes |

### `git/risk-distribution-chart` â€” `RiskDistributionChart`

Top-30 risk-scored vertical bar chart with average reference line.
Score = `0.4Â·churn + 0.35Â·busFactor + 0.25Â·trend`, all normalised to
0â€“100.

| Prop | Type | Required |
|------|------|----------|
| `hotspots` | `Hotspot[]` (`@repowise-dev/types/git`) | yes |

### `git/bus-factor-panel` â€” `BusFactorPanel`

Stacked bar of safe / warning / risk counts plus a top-5 highest-risk
files list.

| Prop | Type | Required |
|------|------|----------|
| `hotspots` | `Hotspot[]` (`@repowise-dev/types/git`) | yes |

---

## `jobs/job-log` — `JobLog`

Auto-scrolling fixed-height log viewer for streaming job progress
entries.

| Prop | Type | Required | Notes |
|------|------|----------|-------|
| `entries` | `Array<{ text: string; level?: number }>` | yes | Caller accumulates from SSE / poll. |
| `maxLines` | `number` | no | Tail length; default `6`. |

## `jobs/generation-progress` — `GenerationProgress`

Status header, progress bar, live cost, summary tiles, and embedded
`JobLog` for a documentation-generation job. Presentational — the
caller polls / streams the job and forwards the snapshot via props.

| Prop | Type | Required | Notes |
|------|------|----------|-------|
| `job` | `GenerationProgressJob \| undefined` | yes | Local subset of the engine `Job` shape (id, status, total/completed_pages, current_level, started_at, error_message, config). |
| `log` | `Array<{ text: string }>` | yes | Caller accumulates SSE log events. |
| `elapsed` | `number` | yes | Wall-clock elapsed in ms. Caller owns the interval. |
| `actualCost` | `number \| null` | yes | Live USD cost; `null` until the first cost-bearing event. |
| `stuckPending` | `boolean` | yes | Caller decides — typically `pending && !started_at && elapsed > threshold`. |
| `cancelling` | `boolean` | yes | Disables the Cancel button while in flight. |
| `onCancel` | `() => void` | yes | Caller wires to the cancel mutation; toasts/error handling live there. |

---

## `symbols/symbol-graph-panel` — `SymbolGraphPanel`

Right-column graph-intelligence panel for a focused symbol: pagerank /
betweenness percentile bars, in/out degree, community label, optional
entry-point score, callers/callees, and heritage (extends/implements).
Presentational.

| Prop | Type | Required | Notes |
|------|------|----------|-------|
| `metrics` | `GraphMetrics \| undefined` (`@repowise-dev/types/graph`) | yes | |
| `metricsLoading` | `boolean` | yes | |
| `callData` | `CallersCallees \| undefined` (`@repowise-dev/types/graph`) | yes | |
| `callsLoading` | `boolean` | yes | |
| `heritageData` | `CallersCallees \| undefined` | no | When omitted or empty, the heritage section is hidden. |

## `symbols/symbol-drawer` — `SymbolDrawer`

Modal dialog over a focused symbol: signature, docstring, parent, plus
an optional right-column slot for the graph-intelligence panel.

| Prop | Type | Required | Notes |
|------|------|----------|-------|
| `symbol` | `CodeSymbol \| null` (`@repowise-dev/types/symbols`) | yes | `null` closes the dialog. |
| `onClose` | `() => void` | yes | |
| `graphPanel` | `ReactNode` | no | Caller passes a data-coupled `<SymbolGraphPanelWrapper>`; omit to hide the column. |

## `symbols/symbol-table` — `SymbolTable`, `SymbolTableProps`, `SortCol`, `SortDir`

Filterable, sortable table of symbols with importance bars and a
configurable drawer slot. Server-driven filters (`q`, `kind`,
`language`) are controlled props because they affect fetch keys; sort
state is UI-local.

| Prop | Type | Required | Notes |
|------|------|----------|-------|
| `items` | `CodeSymbol[]` | yes | Caller fetches; flattened across pages. |
| `importanceScores` | `Map<string, number>` | yes | Normalised 0–1, indexed by `symbol.id`. Caller computes from pagerank × log(complexity). |
| `isLoading` / `isValidating` | `boolean` | yes | First-load skeleton vs. load-more spinner. |
| `hasMore` | `boolean` | yes | Renders the Load-more button. |
| `q` / `onQChange` | `string` / `(v) => void` | yes | Search query. Caller debounces before driving the fetch. |
| `kind` / `onKindChange` | `string` / `(v) => void` | yes | Filter union; `"all"` is the off state. |
| `language` / `onLanguageChange` | `string` / `(v) => void` | yes | Same as kind. |
| `onLoadMore` | `() => void` | yes | Wired to the SWR `setSize` increment. |
| `onSelect` | `(sym: CodeSymbol) => void` | yes | Caller drives the drawer state. |
| `drawer` | `ReactNode` | no | Caller renders `<SymbolDrawer>` with its own data wiring. |

---

## `wiki/code-block` â€” `CodeBlock`

Server-rendered fenced code block. The caller passes pre-highlighted
HTML (e.g. from Shiki) plus the raw code for copy-to-clipboard.

| Prop | Type | Required | Notes |
|------|------|----------|-------|
| `code` | `string` | yes | Raw text used for the copy action. |
| `language` | `string` | no | Shown in the header bar. |
| `html` | `string` | yes | Trusted HTML, set via `dangerouslySetInnerHTML`. |

## `wiki/confidence-badge` â€” `ConfidenceBadge`

Coloured pill showing freshness status (`fresh` / `stale` / `outdated`).
Computes status from `score` unless `status` is supplied. When stale
with `staleSince`, wraps in a tooltip exposing the date and confidence.

| Prop | Type | Required |
|------|------|----------|
| `score` | `number` (0â€“1) | yes |
| `status` | `string` | no |
| `showScore` | `boolean` | no |
| `staleSince` | `string \| null` | no |
| `className` | `string` | no |

## `wiki/git-history-panel` â€” `GitHistoryPanel`

Sidebar panel summarising file lifecycle, commit categories, top
authors with bars, co-change partners, and recent commits.

| Prop | Type | Required |
|------|------|----------|
| `git` | `GitMetadata` (`@repowise-dev/types/git`) | yes |

## `wiki/mermaid-diagram` â€” `MermaidDiagram`

Client-side mermaid renderer with dynamic import to avoid SSR. Uses a
dark theme matched to the design tokens. Failures render an inline
error card.

| Prop | Type | Required |
|------|------|----------|
| `chart` | `string` | yes |

## `wiki/table-of-contents` â€” `TableOfContents`

Extracts `#`/`##`/`###` headings from a markdown string and renders an
anchor list. An `IntersectionObserver` highlights the active heading.
Returns `null` when fewer than two headings are found.

| Prop | Type | Required |
|------|------|----------|
| `content` | `string` | yes |

## `graph/*` — Graph layout, presentation, and chrome

Presentational graph pieces — chrome (toolbar/legend/sidebar/menu),
ELK layout primitives, `@xyflow/react` node/edge components, and the
floating `GraphTooltip`. The `GraphContext` provider also lives here
so node/edge components can be consumed independently of the
`graph-flow` data-fetching host.

`graph-flow` itself, plus the data-coupled panels (`graph-doc-panel`,
`graph-community-panel`, `path-finder-panel`), still live in
`packages/web` because they orchestrate `useGraph*` SWR hooks. They
will move under a wrapper-pattern refactor in a follow-up.

### `graph/context` — `GraphContext`, `GraphProvider`, `useGraphContext`, `GraphContextValue`

Standalone context module. Node and edge components consume it to
read selection / hover / highlight state without depending on the
`graph-flow` host implementation. Hosts wrap their tree in
`<GraphProvider value={...}>`.

| Export | Type | Notes |
|--------|------|-------|
| `GraphContext` | `React.Context<GraphContextValue>` | Default value renders inert. |
| `GraphProvider` | `React.Provider<GraphContextValue>` | Alias of `GraphContext.Provider`. |
| `useGraphContext` | `() => GraphContextValue` | Sugar for `useContext(GraphContext)`. |
| `GraphContextValue` | interface | `highlightedPath`, `highlightedEdges`, `colorMode`, `riskScores`, `hoveredNodeId`, `connectedNodeIds`, `connectedEdgeIds`, `selectedNodeId`, `searchDimmedNodes`. |

### `graph/elk-layout` and `graph/use-elk-layout`

Pure layout primitives. Functions: `layoutFileGraph`,
`layoutModuleGraph`, `groupNodesAsModules`. Hooks:
`useFileElkLayout`, `useModuleElkLayout`. Operate on canonical
`@repowise-dev/types/graph` shapes (`GraphNode`, `GraphLink`,
`ModuleNode`, `ModuleEdge`) and produce `@xyflow/react` `Node[]` /
`Edge[]`. No data fetching.

### `graph/nodes/*`, `graph/edges/*`

`FileNode`, `ModuleGroupNode`, `DependencyEdge` — `@xyflow/react`
node/edge renderers. Consume `GraphContext` for selection / hover /
path-highlight state. Wear `FileNodeData` / `ModuleNodeData` /
`DependencyEdgeData` types from `graph/elk-layout`.

### `graph/graph-tooltip` — `GraphTooltip`

Floating tooltip over a focused node, smart-positioned against the
viewport edges. Renders file or module metadata depending on
`nodeType`.

| Prop | Type | Required |
|------|------|----------|
| `nodeId` | `string` | yes |
| `nodeType` | `string` (`fileNode` / `moduleGroup`) | yes |
| `data` | `Record<string, unknown>` (cast to `FileNodeData` or `ModuleNodeData`) | yes |
| `x`, `y` | `number` | yes |
| `onClose` / `onViewDocs` | `() => void` | yes |
| `onExplore` | `() => void` | no — when omitted, the action button is hidden |

### `graph/graph-context-menu` â€” `GraphContextMenu`

Floating right-click menu rendered at `(x, y)`. Pure UI; the host
wires the four action callbacks.

| Prop | Type | Required |
|------|------|----------|
| `x`, `y` | `number` | yes |
| `nodeId` | `string` | yes |
| `isModule` | `boolean` | yes |
| `onViewDocs` / `onExplore` / `onPathFrom` / `onPathTo` | `() => void` | yes |

### `graph/graph-toolbar` â€” `GraphToolbar`

Top-right toolbar exposing view mode, color mode, hide-tests toggle,
fit-view, path-finder toggle, flows toggle, and a search input. Also
exports the `ColorMode` (`language` / `community` / `risk`) and
`ViewMode` (`module` / `full` / `architecture` / `dead` / `hotfiles`)
unions consumed by `GraphLegend` and the host.

| Prop | Type | Required |
|------|------|----------|
| `viewMode` / `onViewChange` | `ViewMode` / `(v) => void` | yes |
| `colorMode` / `onColorModeChange` | `ColorMode` / `(v) => void` | yes |
| `hideTests` / `onHideTestsChange` | `boolean` / `(v) => void` | yes |
| `onFitView` | `() => void` | yes |
| `showPathFinder` / `onTogglePathFinder` | `boolean` / `() => void` | yes |
| `showFlows` / `onToggleFlows` | `boolean` / `() => void` | yes |
| `searchQuery` / `onSearchChange` | `string` / `(q) => void` | yes |

### `graph/graph-legend` â€” `GraphLegend`

Collapsible legend that picks a key per `colorMode`. Imports the
`ColorMode` and `ViewMode` types from `graph-toolbar`.

| Prop | Type | Required |
|------|------|----------|
| `nodeCount` / `edgeCount` | `number` | yes |
| `colorMode` | `ColorMode` | yes |
| `viewMode` | `ViewMode` | yes |
| `communityLabels` | `Map<number, string>` | no |

### `graph/graph-ego-sidebar` â€” `GraphEgoSidebar`

Right-edge sidebar showing the centre node's neighborhood â€” inbound
/ outbound counts, git metadata (owner, last commit, 30d commits),
and a list of neighbour nodes. Consumes the canonical `EgoGraph`
type, which now optionally carries `center_git_meta: GitMetadata`.

| Prop | Type | Required |
|------|------|----------|
| `graph` | `EgoGraph` (`@repowise-dev/types/graph`) | yes |
| `onClose` | `() => void` | yes |
| `onNavigateToNode` | `(nodeId: string) => void` | no |

---

## `workspace/*` â€” Multi-repo workspace views

Five presentational components that consume the canonical workspace
shapes from `@repowise-dev/types/workspace`.

### `workspace/contract-type-badge` â€” `ContractTypeBadge`, `RoleBadge`

Inline pill badges for contract type (`http`, `grpc`, `topic`, â€¦) and
role (`provider` / `consumer`).

| Component | Prop | Type |
|-----------|------|------|
| `ContractTypeBadge` | `type` | `string` |
| `RoleBadge` | `role` | `string` |

### `workspace/repo-card` â€” `RepoCard`

Tile linking to `/repos/{id}/overview` with file count, doc-coverage,
hotspot count, and a primary indicator.

| Prop | Type | Required |
|------|------|----------|
| `repoId` | `string` | yes |
| `alias` | `string` | yes |
| `name` | `string` | yes |
| `path` | `string` | yes |
| `isPrimary` | `boolean` | yes |
| `stats` | `RepoStats \| null` (`@repowise-dev/types/workspace`) | yes |
| `gitSummary` | `GitSummary \| null` (`@repowise-dev/types/git`) | yes |

### `workspace/co-change-table` â€” `CoChangeTable`

Sortable cross-repo co-change table.

| Prop | Type | Required | Notes |
|------|------|----------|-------|
| `coChanges` | `WorkspaceCoChangeEntry[]` | yes | |
| `compact` | `boolean` | no | Drops the frequency and last-date columns. |

### `workspace/contract-links-table` â€” `ContractLinksTable`

Provider/consumer table for matched contract links with confidence
bars colour-graded against the freshness thresholds.

| Prop | Type | Required |
|------|------|----------|
| `links` | `WorkspaceContractLinkEntry[]` | yes |

### `workspace/cross-repo-summary` â€” `CrossRepoSummary`

Four-tile summary header (`StatCard` Ã— 4) for co-change pairs, package
deps, contract links, and contract count.

| Prop | Type | Required |
|------|------|----------|
| `crossRepo` | `WorkspaceCrossRepoSummary \| null` | yes |
| `contracts` | `WorkspaceContractSummary \| null` | yes |

---

## `dashboard/*` â€” Repo overview tiles

Nine presentational tiles that consume canonical engine artifacts.
The data-coupled trio (`active-job-banner`, `quick-actions`,
`community-summary-grid`) stays in `packages/web` for now.

### `dashboard/attention-panel` â€” `AttentionPanel`

Categorised list of items needing developer attention. Re-exports the
`AttentionItem` type so `packages/web`'s `health-score.ts` can derive
items without recomputing the shape.

| Prop | Type | Required |
|------|------|----------|
| `items` | `AttentionItem[]` | yes |
| `repoId` | `string` | yes |

### `dashboard/decisions-timeline` â€” `DecisionsTimeline`

Top-6 most recent decisions with status dots and a "view all" link.

| Prop | Type | Required |
|------|------|----------|
| `decisions` | `DecisionRecord[]` (`@repowise-dev/types/decisions`) | yes |
| `repoId` | `string` | yes |

### `dashboard/dependency-heatmap` â€” `DependencyHeatmap`

20Ã—20 canvas heatmap of module-to-module edge counts. Modules sorted
by `avg_pagerank`. Returns `null` when fewer than two modules are
available.

| Prop | Type | Required |
|------|------|----------|
| `moduleGraph` | `ModuleGraph` (`@repowise-dev/types/graph`) | yes |

### `dashboard/execution-flows-panel` â€” `ExecutionFlowsPanel`

Top-8 execution flows with collapsible call traces. `repoId` is
accepted for parity with other dashboard tiles but currently unused.

| Prop | Type | Required |
|------|------|----------|
| `flows` | `ExecutionFlowEntry[]` (`@repowise-dev/types/graph`) | yes |
| `repoId` | `string` | yes |

### `dashboard/health-score-ring` â€” `HealthScoreRing`

Animated `framer-motion` SVG ring showing 0â€“100 score with text label.

| Prop | Type | Required |
|------|------|----------|
| `score` | `number` (0â€“100) | yes |
| `size` | `number` | no, default `160` |

### `dashboard/hotspots-mini` â€” `HotspotsMini`

Top-5 hotspots tile.

| Prop | Type | Required |
|------|------|----------|
| `hotspots` | `Hotspot[]` (`@repowise-dev/types/git`) | yes |
| `repoId` | `string` | yes |

### `dashboard/language-donut` â€” `LanguageDonut`

Top-6 languages-by-file-count donut with grouped "other" bucket.

| Prop | Type | Required |
|------|------|----------|
| `distribution` | `Record<string, number>` | yes |

### `dashboard/module-minimap` â€” `ModuleMinimap`

Force-directed module graph using `d3-force`. Doc-coverage colours
the nodes. The simulation runs synchronously for small graphs (â‰¤150
ticks) and re-renders once the layout converges.

| Prop | Type | Required |
|------|------|----------|
| `nodes` | `ModuleNode[]` (`@repowise-dev/types/graph`) | yes |
| `edges` | `ModuleEdge[]` (`@repowise-dev/types/graph`) | yes |
| `repoId` | `string` | yes |

### `dashboard/ownership-treemap` â€” `OwnershipTreemap`

`d3-hierarchy` treemap colouring rectangles by primary owner; silos
render at lower opacity. Distinct from `git/ownership-treemap` â€”
this variant is the dashboard tile (Card-wrapped, fixed height,
legend below).

| Prop | Type | Required |
|------|------|----------|
| `entries` | `OwnershipEntry[]` (`@repowise-dev/types/git`) | yes |

---

## `chat/*` â€” Conversation rendering primitives

The chat UI types are sourced from `@repowise-dev/types/chat` â€”
`ChatUIMessage` and `ChatUIToolCall` are the post-streaming flattened
shapes consumed by these components. The wire types (`ChatMessage`,
`ChatToolCall`) live in the same module; consumers are expected to
keep the SSE merge in their own data layer.

### `chat/chat-markdown` â€” `ChatMarkdown`

Compact markdown renderer (`react-markdown` + `remark-gfm`) tuned for
chat density. Code fences are inline `<pre><code>` blocks â€” no copy
affordance.

| Prop | Type | Required |
|------|------|----------|
| `content` | `string` | yes |

### `chat/tool-call-block` â€” `ToolCallBlock`

Collapsible card showing a single tool invocation. Friendly labels for
known tool names; falls back to the raw name. Shows a "View" button
that fires `onViewArtifact` when an artifact is attached.

| Prop | Type | Required |
|------|------|----------|
| `toolCall` | `ChatUIToolCall` (`@repowise-dev/types/chat`) | yes |
| `onViewArtifact` | `() => void` | no |

### `chat/source-citations` â€” `SourceCitations`

Renders an inline list of source-page links extracted from
`tool_calls[]`. Also exports `extractSources(toolCalls, repoId)` for
callers that need the same shape outside of rendering. Uses plain
`<a>` (no Next-only `Link`) so the package stays framework-neutral â€”
consumers wire prefetching at the parent level if needed.

| Prop | Type | Required |
|------|------|----------|
| `toolCalls` | `ChatUIToolCall[]` | yes |
| `repoId` | `string` | yes |

### `chat/chat-message` â€” `ChatMessage`

Renders one user or assistant turn: avatar, message bubble for user,
tool-call cards + markdown + citations for assistant. Streaming
indicator shows when `message.isStreaming` and there's no text yet.

| Prop | Type | Required | Notes |
|------|------|----------|-------|
| `message` | `ChatUIMessage` | yes | |
| `repoId` | `string` | yes | Forwarded to `SourceCitations` for link targets. |
| `onViewArtifact` | `(artifact: { type; data }) => void` | no | Wired through to `ToolCallBlock` when an artifact is attached. |
| `assistantAvatarSrc` | `string` | no | Defaults to `/repowise-logo.png`. Override in consumers that don't host that asset. |

### `chat/artifact-panel` â€” `ArtifactPanel`

Right-edge slide-over panel. Switches on `artifact.type` to pick a
renderer: markdown for `overview` / `wiki_page`, mermaid (via
`@repowise-dev/ui/wiki/mermaid-diagram`) for `diagram`, list for
`search_results`, JSON pretty-print fallback otherwise.

| Prop | Type | Required |
|------|------|----------|
| `artifacts` | `Artifact[]` (locally-defined wrapper: `{ type; title; data }`) | yes |
| `open` | `boolean` | yes |
| `onClose` | `() => void` | yes |

---

## `wiki/wiki-markdown` â€” `WikiMarkdown`

Client-side markdown renderer (`react-markdown` + `remark-gfm`) with
slugged heading anchors, copy-on-hover code blocks, and inline
`MermaidDiagram` for `\`\`\`mermaid` fences.

| Prop | Type | Required |
|------|------|----------|
| `content` | `string` | yes |

---

## `blast-radius/*` — PR impact analysis shells

Pure presentational shells for the blast-radius view. Data fetching,
input form, and SWR hotspot suggestions stay in the consumer page;
these components only render `BlastRadiusResponse` (from
`@repowise-dev/types/blast-radius`).

### `blast-radius/risk-score-card` — `RiskScoreCard`

Coloured 0–10 gauge. Red ≥7, amber ≥4, emerald otherwise.

| Prop | Type | Required |
|------|------|----------|
| `score` | `number` (0–10) | yes |

### `blast-radius/table-section` — `TableSection`

Card wrapper with title and "empty" placeholder.

| Prop | Type | Required |
|------|------|----------|
| `title` | `string` | yes |
| `empty` | `boolean` | yes |
| `emptyLabel` | `string` (defaults to "None") | no |
| `children` | `ReactNode` | yes |

### `blast-radius/direct-risks-table` — `DirectRisksTable`

Renders `DirectRiskEntry[]`. Multiplies `risk_score` and
`temporal_hotspot` by 10 for display (backend ships 0–1).

| Prop | Type | Required |
|------|------|----------|
| `rows` | `DirectRiskEntry[]` | yes |

### `blast-radius/transitive-table` — `TransitiveTable`

Two-column path + dependency depth.

| Prop | Type | Required |
|------|------|----------|
| `rows` | `TransitiveEntry[]` | yes |

### `blast-radius/cochange-table` — `CochangeTable`

Three-column changed file / missing partner / score.

| Prop | Type | Required |
|------|------|----------|
| `rows` | `CochangeWarning[]` | yes |

### `blast-radius/reviewers-table` — `ReviewersTable`

Email + files-owned + percent ownership. `ownership_pct` is 0–1.

| Prop | Type | Required |
|------|------|----------|
| `rows` | `ReviewerEntry[]` | yes |

### `blast-radius/test-gaps-list` — `TestGapsList`

Bullet list of files lacking adjacent test coverage.

| Prop | Type | Required |
|------|------|----------|
| `gaps` | `string[]` | yes |

### `blast-radius/blast-radius-summary` — `BlastRadiusSummary`

Four-stat card sitting beside `RiskScoreCard` (Direct Risks /
Transitive Files / Co-change Warnings / Test Gaps).

| Prop | Type | Required |
|------|------|----------|
| `result` | `BlastRadiusResponse` | yes |

### `blast-radius/blast-radius-results` — `BlastRadiusResults`

Composes the full results stack: gauge + summary + the five tables.

| Prop | Type | Required |
|------|------|----------|
| `result` | `BlastRadiusResponse` | yes |
