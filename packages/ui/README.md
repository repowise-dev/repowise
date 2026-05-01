# @repowise/ui

Shared visualization components for the Repowise dashboard
(`packages/web`) and any downstream consumer that wants to render the
same engine artifacts.

## Layout

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

Consumers must add `transpilePackages: ["@repowise/ui", "@repowise/types"]`
to their `next.config.ts` (Tailwind v4 + TS source ship as-is, no
pre-build step).

To inherit the canonical design tokens, import the stylesheet once at
the root of the app:

```css
@import "@repowise/ui/styles.css";
```

## Peer deps

`react`, `react-dom`. Components stay client-pure or pure-presentational —
they do not call `next/navigation` or `next/link` directly. Where routing
is needed, accept it via props or context so consumers can wire their
own router.
