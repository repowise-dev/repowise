# @repowise/ui

Shared visualization components for the Repowise OSS dashboard
(`packages/web`) and the hosted web app (currently `frontend/`, becoming
`packages/hosted-web/` in Phase 1B per Option α — see
`docs/HOSTED_PRODUCT_UPGRADE_PLAN.md` §5).

## Status

**Phase 1A: scaffold only.** Package layout, exports map, and design-token
file are stubbed. No components have been moved in yet — that is Phase 1B's
job.

## Layout (per plan §5.2)

```
src/
  graph/         graph-flow, graph-toolbar, ego-sidebar, …
  git/           hotspot-table, ownership-treemap, …
  dead-code/
  decisions/
  docs/
  symbols/
  coverage/
  wiki/
  chat/          artifact-panel, model-selector, source-citations
  dashboard/     health-score-ring, attention-panel, …
  workspace/     repo-card, contracts-table, …
  jobs/          generation-progress, job-log
  shared/        stat-card, empty-state, api-error
  hooks/         use-elk-layout, use-sse, …
  ui/            Radix-CVA primitives
styles/
  globals.css    canonical design tokens (Tailwind v4 @theme)
```

## Consuming

Components are exposed via subpath exports — import the slice you need
rather than the barrel:

```ts
import { HotspotTable } from "@repowise/ui/git";
import { GraphFlow } from "@repowise/ui/graph";
```

Both consumers must `transpilePackages: ["@repowise/ui"]` in their
`next.config.ts` (Tailwind v4 + TS source ship as-is, no pre-build).

## Peer deps

`react`, `react-dom`. Components stay client-pure or pure-presentational —
they do not call `next/navigation` or `next/link` directly. Where routing
is needed, accept it via props or context (per plan §5.4).
