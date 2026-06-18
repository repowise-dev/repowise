"use client";

import {
  DocsPageActions as DocsPageActionsShell,
  ExportMenu as ExportMenuShell,
  SidebarToggle,
} from "@repowise-dev/ui/docs/docs-page-actions";
import { downloadTextFile } from "@/lib/utils/download";
import type { ReaderPersona } from "@repowise-dev/ui/docs/reader-persona";
import type { PageResponse } from "@/lib/api/types";

export { SidebarToggle };

/** Web wrapper: maps the page object onto the presentational actions shell. */
export function DocsPageActions({
  page,
  persona,
  setPersona,
  personaHasEffect,
}: {
  page: PageResponse;
  persona: ReaderPersona;
  setPersona: (next: ReaderPersona) => void;
  personaHasEffect: boolean;
}) {
  return (
    <DocsPageActionsShell
      confidence={page.confidence}
      freshnessStatus={page.freshness_status}
      persona={persona}
      setPersona={setPersona}
      personaHasEffect={personaHasEffect}
    />
  );
}

/**
 * Web wrapper around the Export menu shell — owns the actual download
 * side-effects (single-page + all-pages markdown writes).
 */
export function ExportMenu({
  isExporting,
  onExportAll,
  zipHref,
  page,
}: {
  isExporting: boolean;
  onExportAll: () => void;
  zipHref: string;
  page: PageResponse | null;
  /** Accepted for call-site compatibility; the shell builds hrefs itself. */
  repoId?: string;
}) {
  const exportPage = () => {
    if (!page) return;
    const filename = (page.target_path || page.title).replace(/\//g, "_") + ".md";
    const header = `# ${page.title}\n\n> Path: ${page.target_path}\n\n`;
    downloadTextFile(header + page.content, filename);
  };

  return (
    <ExportMenuShell
      isExporting={isExporting}
      onExportPage={exportPage}
      onExportAll={onExportAll}
      zipHref={zipHref}
      hasPage={!!page}
    />
  );
}
