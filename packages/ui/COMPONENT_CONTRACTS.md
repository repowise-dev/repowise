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
