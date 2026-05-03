# @repowise-dev/ui

Shared visualization components for the Repowise dashboard
(`packages/web`) and any downstream consumer that wants to render the
same engine artifacts.

## Layout

```
src/
  graph/         graph-flow, graph-toolbar, ego-sidebar, â€¦
  git/           hotspot-table, ownership-treemap, â€¦
  dead-code/
  decisions/
  docs/
  symbols/
  coverage/
  wiki/
  chat/          artifact-panel, model-selector, source-citations
  dashboard/     health-score-ring, attention-panel, â€¦
  workspace/     repo-card, contracts-table, â€¦
  jobs/          generation-progress, job-log
  shared/        stat-card, empty-state, api-error
  hooks/         use-elk-layout, use-sse, â€¦
  ui/            Radix-CVA primitives
styles/
  globals.css    canonical design tokens (Tailwind v4 @theme)
```

## Consuming

Components are exposed via subpath exports â€” import the slice you need
rather than the barrel:

```ts
import { HotspotTable } from "@repowise-dev/ui/git";
import { GraphFlow } from "@repowise-dev/ui/graph";
```

Consumers must add `transpilePackages: ["@repowise-dev/ui", "@repowise-dev/types"]`
to their `next.config.ts` (Tailwind v4 + TS source ship as-is, no
pre-build step).

To inherit the canonical design tokens, import the stylesheet once at
the root of the app:

```css
@import "@repowise-dev/ui/styles.css";
```

## Peer deps

`react`, `react-dom`. Components stay client-pure or pure-presentational â€”
they do not call `next/navigation` or `next/link` directly. Where routing
is needed, accept it via props or context so consumers can wire their
own router.
