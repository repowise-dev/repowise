import type { ElementType, ReactNode } from "react";
import type { FileDetailResponse } from "@repowise-dev/types/files";
import { FileOverviewTab } from "./file-overview-tab";
import { FileDocTab } from "./file-doc-tab";
import { FileHistoryTab } from "./file-history-tab";
import { FileDecisionsTab } from "./file-decisions-tab";
import { FileCoverageTab } from "./file-coverage-tab";
import { FileGraphTab } from "./file-graph-tab";
import type { FilePageTab } from "./file-page-tabs";

export interface BuildFilePanelsOptions {
  data: FileDetailResponse;
  /** Route prefix for the repo, e.g. `/repos/:id`. */
  linkPrefix: string;
  /** Build a file-page href. */
  fileHref: (path: string) => string;
  /** Build a symbol-page href. */
  symbolHref: (symbolId: string) => string;
  /** Server-rendered wiki content for the Doc tab. */
  docSlot?: ReactNode;
  /** Shiki HTML with per-line `data-covered` attributes (Coverage tab). */
  coverageCodeHtml?: string | undefined;
  /**
   * The Health panel. It is the one tab body that is genuinely interactive, so
   * the host supplies it already wrapped in whatever client component owns the
   * triage callback — a function cannot cross a server boundary as a prop.
   */
  healthPanel?: ReactNode;
  /**
   * The graph-inferred test list for the Tests tab. Supplied already wrapped by
   * the host for the same reason as `healthPanel`: it fetches on the client, and
   * a fetcher function cannot cross the server boundary as a prop.
   */
  testsPanel?: ReactNode;
  /**
   * Router link, forwarded to every tab body that renders one.
   *
   * The tab bodies are server components and stay server components: a
   * `next/link` element rendered *from* a server component is still server
   * markup, so this does not drag any of them back across the hydration
   * boundary the way an `onClick` would. Threading it here rather than letting
   * a body import a router is the same reason the Decisions tab already took
   * it — `packages/ui` must not know what framework mounts it.
   */
  LinkComponent?: ElementType | undefined;
}

/**
 * Render the six pure tab bodies **on the server** and hand them to the client
 * shell as markup.
 *
 * This is where the hydration boundary moved to. `FilePage` used to be a client
 * component that imported all seven bodies, so the whole slice — donut,
 * sparkline, tier bar, treemap helpers, seven icon sets — shipped to the
 * browser along with a `FileDetailResponse` serialised twice (into the flight
 * payload and inlined into the HTML), on a page that renders one tab. Only
 * `FileHealthTab` needs hydrating now.
 *
 * A plain function rather than a component because the shell addresses panels
 * individually; components cannot return a record. It must be called from a
 * server component.
 */
export function buildFilePanels({
  data,
  linkPrefix,
  fileHref,
  symbolHref,
  docSlot,
  coverageCodeHtml,
  healthPanel,
  testsPanel,
  LinkComponent,
}: BuildFilePanelsOptions): Partial<Record<FilePageTab, ReactNode>> {
  const link = LinkComponent ? { LinkComponent } : {};
  return {
    overview: (
      <FileOverviewTab
        data={data}
        symbolHref={symbolHref}
        fileHref={fileHref}
        {...link}
      />
    ),
    doc: <FileDocTab wikiPage={data.wiki_page} docSlot={docSlot} />,
    health: healthPanel,
    history: (
      <FileHistoryTab
        git={data.git}
        linkPrefix={linkPrefix}
        partnerHref={fileHref}
        {...link}
      />
    ),
    decisions: (
      <FileDecisionsTab
        decisions={data.governing_decisions ?? []}
        linkPrefix={linkPrefix}
        {...link}
      />
    ),
    graph: (
      <FileGraphTab
        graph={data.graph}
        filePath={data.file_path}
        linkPrefix={linkPrefix}
        fileHref={fileHref}
        symbolHref={symbolHref}
        {...link}
      />
    ),
    coverage: (
      <FileCoverageTab
        coverage={data.coverage}
        coverageCodeHtml={coverageCodeHtml}
        testsPanel={testsPanel}
      />
    ),
  };
}
