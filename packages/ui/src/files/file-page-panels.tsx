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
  /** Deep link into the docs reading surface. */
  wikiHref?: string | undefined;
  /**
   * The Health panel. It is the one tab body that is genuinely interactive, so
   * the host supplies it already wrapped in whatever client component owns the
   * triage callback — a function cannot cross a server boundary as a prop.
   */
  healthPanel?: ReactNode;
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
  wikiHref,
  healthPanel,
  LinkComponent,
}: BuildFilePanelsOptions): Partial<Record<FilePageTab, ReactNode>> {
  return {
    overview: <FileOverviewTab data={data} symbolHref={symbolHref} fileHref={fileHref} />,
    doc: <FileDocTab wikiPage={data.wiki_page} docSlot={docSlot} wikiHref={wikiHref} />,
    health: healthPanel,
    history: (
      <FileHistoryTab git={data.git} linkPrefix={linkPrefix} partnerHref={fileHref} />
    ),
    decisions: (
      <FileDecisionsTab
        decisions={data.governing_decisions ?? []}
        linkPrefix={linkPrefix}
        {...(LinkComponent ? { LinkComponent } : {})}
      />
    ),
    graph: (
      <FileGraphTab
        graph={data.graph}
        filePath={data.file_path}
        linkPrefix={linkPrefix}
        fileHref={fileHref}
        symbolHref={symbolHref}
      />
    ),
    coverage: (
      <FileCoverageTab coverage={data.coverage} coverageCodeHtml={coverageCodeHtml} />
    ),
  };
}
