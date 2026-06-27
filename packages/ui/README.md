# @repowise-dev/ui

Shared visualization components for the Repowise dashboard
(`packages/web`) and any downstream consumer that wants to render the
same engine artifacts.

## Layout

```
src/
  blast-radius/  PR impact analysis shells
  c4/            C4 architecture views (+ mobile/ layout primitives)
  chat/          chat-interface, artifact-panel, chat-markdown, …
  commits/       commit explorer table + detail
  costs/         LLM cost breakdowns
  coverage/      coverage-donut, freshness-table
  dashboard/     health-score-ring, attention-panel, overview tiles, …
  dead-code/     summary-bar, findings shells
  decisions/     decisions-table, evidence drawer, verification badge
  docs/          docs-tree, doc-nav, reader-persona
  files/         file entity page (doc/health/history/coverage/graph tabs)
  git/           hotspot-table, ownership-table/treemap, churn viz, …
  graph/         graph-flow (Sigma), toolbar, legend, panels (+ sigma/)
  graph-primitives/
  health/        file-table, kpi-cards, markers, score tokens
  hooks/         use-debounce, …
  jobs/          generation-progress, job-log
  modules/       module health detail
  onboarding/    first-run surfaces
  owners/        owner directory + profile
  security/      findings table
  settings/      general-form
  shared/        primitives: responsive-table, adaptive-panel, toast,
                 error-boundary, empty-state, api-error, stat-card,
                 entity links/hover cards, context-drawer, owl-loader
  symbols/       symbol-table, symbol-drawer, symbol-page, graph/git panels
  ui/            Radix-CVA primitives
  wiki/          wiki-markdown, code-block, ToC, git-history, backlinks
  workspace/     multi-repo tables + summary
styles/
  globals.css    canonical design tokens (Tailwind v4 @theme)
```

## Consuming

Components are exposed via subpath exports — import the slice you need
rather than the barrel:

```ts
import { HotspotTable } from "@repowise-dev/ui/git";
import { GraphFlow } from "@repowise-dev/ui/graph";
import { ResponsiveTable, AdaptivePanel, Toaster, toast } from "@repowise-dev/ui/shared";
```

Consumers must add `transpilePackages: ["@repowise-dev/ui", "@repowise-dev/types"]`
to their `next.config.ts` (Tailwind v4 + TS source ship as-is, no
pre-build step).

To inherit the canonical design tokens, import the stylesheet once at
the root of the app:

```css
@import "@repowise-dev/ui/styles.css";
```

## Shared primitives

- **`ResponsiveTable`** — the table primitive every list surface should
  build on. Columns declare a `priority` (1 always visible, 2 hidden
  below `md`, 3 hidden below `lg`); the wrapper always falls back to
  `overflow-x-auto`, and `stacked` collapses the table into cards on
  phones.
- **`AdaptivePanel`** — one overlay surface: right-side panel on
  desktop, swipe-dismissable bottom sheet on mobile. `ContextDrawer`
  and the chat `ArtifactPanel` are built on it.
- **`Toaster` / `toast`** — token-themed sonner toaster (uses
  `--z-toast`). Mount the `Toaster` once at the app shell; the host
  passes the resolved theme.
- **`ErrorBoundary`** — recoverable render-error boundary; default
  fallback is `ApiError` with a reset retry.
- **`EmptyState`** — the only sanctioned empty-state rendering. No raw
  `<p>` placeholders.
- Score colors come from `health/tokens.ts`. The canonical health *buckets*
  are the 3 defect-backed bands in `@repowise-dev/types/health`
  (`HealthBand` — Alert/Warning/Healthy at `<4 / 4–8 / ≥8`); use
  `healthBandSoftBadgeClass` / `healthBandTextColor` for band colors. The
  `scoreBand` / `scoreBadgeClass` / `scoreSoftBadgeClass` / `scoreTextColor`
  helpers are an internal 4-step color ramp (`<4 / <6 / <8 / ≥8`) for
  file-table pills only — not a labeling scheme — mapped to the semantic
  tokens (`--color-error/warning/caution/success`).

## Peer deps

`react`, `react-dom`, `next-themes` (theme resolution only). Components
stay client-pure or pure-presentational — they do not call
`next/navigation` or `next/link` directly. Where routing is needed,
accept it via props (`renderLink` render props, `LinkComponent`,
`buildHref`) or context so consumers can wire their own router. New
components must not read `window.location`; initial state arrives via
props.
