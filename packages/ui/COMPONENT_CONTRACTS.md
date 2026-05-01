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

## `workspace/*` — Multi-repo workspace views

Five presentational components that consume the canonical workspace
shapes from `@repowise/types/workspace`.

### `workspace/contract-type-badge` — `ContractTypeBadge`, `RoleBadge`

Inline pill badges for contract type (`http`, `grpc`, `topic`, …) and
role (`provider` / `consumer`).

| Component | Prop | Type |
|-----------|------|------|
| `ContractTypeBadge` | `type` | `string` |
| `RoleBadge` | `role` | `string` |

### `workspace/repo-card` — `RepoCard`

Tile linking to `/repos/{id}/overview` with file count, doc-coverage,
hotspot count, and a primary indicator.

| Prop | Type | Required |
|------|------|----------|
| `repoId` | `string` | yes |
| `alias` | `string` | yes |
| `name` | `string` | yes |
| `path` | `string` | yes |
| `isPrimary` | `boolean` | yes |
| `stats` | `RepoStats \| null` (`@repowise/types/workspace`) | yes |
| `gitSummary` | `GitSummary \| null` (`@repowise/types/git`) | yes |

### `workspace/co-change-table` — `CoChangeTable`

Sortable cross-repo co-change table.

| Prop | Type | Required | Notes |
|------|------|----------|-------|
| `coChanges` | `WorkspaceCoChangeEntry[]` | yes | |
| `compact` | `boolean` | no | Drops the frequency and last-date columns. |

### `workspace/contract-links-table` — `ContractLinksTable`

Provider/consumer table for matched contract links with confidence
bars colour-graded against the freshness thresholds.

| Prop | Type | Required |
|------|------|----------|
| `links` | `WorkspaceContractLinkEntry[]` | yes |

### `workspace/cross-repo-summary` — `CrossRepoSummary`

Four-tile summary header (`StatCard` × 4) for co-change pairs, package
deps, contract links, and contract count.

| Prop | Type | Required |
|------|------|----------|
| `crossRepo` | `WorkspaceCrossRepoSummary \| null` | yes |
| `contracts` | `WorkspaceContractSummary \| null` | yes |

---

## `dashboard/*` — Repo overview tiles

Nine presentational tiles that consume canonical engine artifacts.
The data-coupled trio (`active-job-banner`, `quick-actions`,
`community-summary-grid`) stays in `packages/web` for now.

### `dashboard/attention-panel` — `AttentionPanel`

Categorised list of items needing developer attention. Re-exports the
`AttentionItem` type so `packages/web`'s `health-score.ts` can derive
items without recomputing the shape.

| Prop | Type | Required |
|------|------|----------|
| `items` | `AttentionItem[]` | yes |
| `repoId` | `string` | yes |

### `dashboard/decisions-timeline` — `DecisionsTimeline`

Top-6 most recent decisions with status dots and a "view all" link.

| Prop | Type | Required |
|------|------|----------|
| `decisions` | `DecisionRecord[]` (`@repowise/types/decisions`) | yes |
| `repoId` | `string` | yes |

### `dashboard/dependency-heatmap` — `DependencyHeatmap`

20×20 canvas heatmap of module-to-module edge counts. Modules sorted
by `avg_pagerank`. Returns `null` when fewer than two modules are
available.

| Prop | Type | Required |
|------|------|----------|
| `moduleGraph` | `ModuleGraph` (`@repowise/types/graph`) | yes |

### `dashboard/execution-flows-panel` — `ExecutionFlowsPanel`

Top-8 execution flows with collapsible call traces. `repoId` is
accepted for parity with other dashboard tiles but currently unused.

| Prop | Type | Required |
|------|------|----------|
| `flows` | `ExecutionFlowEntry[]` (`@repowise/types/graph`) | yes |
| `repoId` | `string` | yes |

### `dashboard/health-score-ring` — `HealthScoreRing`

Animated `framer-motion` SVG ring showing 0–100 score with text label.

| Prop | Type | Required |
|------|------|----------|
| `score` | `number` (0–100) | yes |
| `size` | `number` | no, default `160` |

### `dashboard/hotspots-mini` — `HotspotsMini`

Top-5 hotspots tile.

| Prop | Type | Required |
|------|------|----------|
| `hotspots` | `Hotspot[]` (`@repowise/types/git`) | yes |
| `repoId` | `string` | yes |

### `dashboard/language-donut` — `LanguageDonut`

Top-6 languages-by-file-count donut with grouped "other" bucket.

| Prop | Type | Required |
|------|------|----------|
| `distribution` | `Record<string, number>` | yes |

### `dashboard/module-minimap` — `ModuleMinimap`

Force-directed module graph using `d3-force`. Doc-coverage colours
the nodes. The simulation runs synchronously for small graphs (≤150
ticks) and re-renders once the layout converges.

| Prop | Type | Required |
|------|------|----------|
| `nodes` | `ModuleNode[]` (`@repowise/types/graph`) | yes |
| `edges` | `ModuleEdge[]` (`@repowise/types/graph`) | yes |
| `repoId` | `string` | yes |

### `dashboard/ownership-treemap` — `OwnershipTreemap`

`d3-hierarchy` treemap colouring rectangles by primary owner; silos
render at lower opacity. Distinct from `git/ownership-treemap` —
this variant is the dashboard tile (Card-wrapped, fixed height,
legend below).

| Prop | Type | Required |
|------|------|----------|
| `entries` | `OwnershipEntry[]` (`@repowise/types/git`) | yes |

---

## `chat/*` — Conversation rendering primitives

The chat UI types are sourced from `@repowise/types/chat` —
`ChatUIMessage` and `ChatUIToolCall` are the post-streaming flattened
shapes consumed by these components. The wire types (`ChatMessage`,
`ChatToolCall`) live in the same module; consumers are expected to
keep the SSE merge in their own data layer.

### `chat/chat-markdown` — `ChatMarkdown`

Compact markdown renderer (`react-markdown` + `remark-gfm`) tuned for
chat density. Code fences are inline `<pre><code>` blocks — no copy
affordance.

| Prop | Type | Required |
|------|------|----------|
| `content` | `string` | yes |

### `chat/tool-call-block` — `ToolCallBlock`

Collapsible card showing a single tool invocation. Friendly labels for
known tool names; falls back to the raw name. Shows a "View" button
that fires `onViewArtifact` when an artifact is attached.

| Prop | Type | Required |
|------|------|----------|
| `toolCall` | `ChatUIToolCall` (`@repowise/types/chat`) | yes |
| `onViewArtifact` | `() => void` | no |

### `chat/source-citations` — `SourceCitations`

Renders an inline list of source-page links extracted from
`tool_calls[]`. Also exports `extractSources(toolCalls, repoId)` for
callers that need the same shape outside of rendering. Uses plain
`<a>` (no Next-only `Link`) so the package stays framework-neutral —
consumers wire prefetching at the parent level if needed.

| Prop | Type | Required |
|------|------|----------|
| `toolCalls` | `ChatUIToolCall[]` | yes |
| `repoId` | `string` | yes |

### `chat/chat-message` — `ChatMessage`

Renders one user or assistant turn: avatar, message bubble for user,
tool-call cards + markdown + citations for assistant. Streaming
indicator shows when `message.isStreaming` and there's no text yet.

| Prop | Type | Required | Notes |
|------|------|----------|-------|
| `message` | `ChatUIMessage` | yes | |
| `repoId` | `string` | yes | Forwarded to `SourceCitations` for link targets. |
| `onViewArtifact` | `(artifact: { type; data }) => void` | no | Wired through to `ToolCallBlock` when an artifact is attached. |
| `assistantAvatarSrc` | `string` | no | Defaults to `/repowise-logo.png`. Override in consumers that don't host that asset. |

### `chat/artifact-panel` — `ArtifactPanel`

Right-edge slide-over panel. Switches on `artifact.type` to pick a
renderer: markdown for `overview` / `wiki_page`, mermaid (via
`@repowise/ui/wiki/mermaid-diagram`) for `diagram`, list for
`search_results`, JSON pretty-print fallback otherwise.

| Prop | Type | Required |
|------|------|----------|
| `artifacts` | `Artifact[]` (locally-defined wrapper: `{ type; title; data }`) | yes |
| `open` | `boolean` | yes |
| `onClose` | `() => void` | yes |

---

## `wiki/wiki-markdown` — `WikiMarkdown`

Client-side markdown renderer (`react-markdown` + `remark-gfm`) with
slugged heading anchors, copy-on-hover code blocks, and inline
`MermaidDiagram` for `\`\`\`mermaid` fences.

| Prop | Type | Required |
|------|------|----------|
| `content` | `string` | yes |
