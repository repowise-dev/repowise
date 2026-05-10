"use client";

import { use, useMemo, useState } from "react";
import useSWR from "swr";
import { Download, FolderArchive, Loader2 } from "lucide-react";
import { Button } from "@repowise-dev/ui/ui/button";
import { FirstFiveFiles, type FirstFiveFile } from "@repowise-dev/ui/onboarding/first-five-files";
import { DocsExplorer } from "@/components/docs/docs-explorer";
import { listAllPages, listPages } from "@/lib/api/pages";
import { getGraph } from "@/lib/api/graph";
import type { DocPage } from "@repowise-dev/types/docs";
import { downloadTextFile } from "@/lib/utils/download";
import type { GraphExportResponse } from "@/lib/api/types";

export default function DocsPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id: repoId } = use(params);
  const [isExporting, setIsExporting] = useState(false);

  const { data: graphData } = useSWR<GraphExportResponse>(
    `graph:${repoId}`,
    () => getGraph(repoId),
    { revalidateOnFocus: false, revalidateOnReconnect: false },
  );

  const { data: docPages } = useSWR<DocPage[]>(
    `docs-list:${repoId}`,
    () => listPages(repoId, { limit: 1000 }),
    { revalidateOnFocus: false },
  );

  const pageByPath = useMemo(() => {
    const m = new Map<string, DocPage>();
    for (const p of docPages ?? []) {
      if (p.target_path) m.set(p.target_path, p);
    }
    return m;
  }, [docPages]);

  const startHere: Array<FirstFiveFile & { page_id?: string }> = useMemo(() => {
    if (!graphData) return [];
    const entries = graphData.nodes.filter((n) => n.is_entry_point);
    const pool = entries.length > 0 ? entries : graphData.nodes;
    return [...pool]
      .sort((a, b) => b.pagerank - a.pagerank)
      .slice(0, 5)
      .map((n) => {
        const page = pageByPath.get(n.node_id);
        return {
          file_path: n.node_id,
          is_entry_point: n.is_entry_point,
          has_doc: page !== undefined,
          pagerank: n.pagerank,
          reason: n.is_entry_point ? "entry point" : "high centrality",
          page_id: page?.id,
        };
      });
  }, [graphData, pageByPath]);

  const handleExportAll = async () => {
    setIsExporting(true);
    try {
      const pages = await listAllPages(repoId);
      pages.sort((a, b) => a.target_path.localeCompare(b.target_path));
      const content = pages
        .map((p) => `# ${p.title}\n\n> ${p.target_path}\n\n${p.content}`)
        .join("\n\n---\n\n");
      downloadTextFile(content, "documentation-export.md");
    } finally {
      setIsExporting(false);
    }
  };

  return (
    <div className="flex flex-col h-screen">
      {/* Header */}
      <div className="shrink-0 px-4 sm:px-6 py-3 border-b border-[var(--color-border-default)] flex items-start justify-between gap-4">
        <div>
          <h1 className="text-lg font-semibold text-[var(--color-text-primary)]">
            Documentation
          </h1>
          <p className="text-xs text-[var(--color-text-secondary)] mt-0.5">
            Browse AI-generated documentation for every file, module, and symbol.
          </p>
        </div>
        <div className="flex gap-2 shrink-0">
          <Button
            variant="outline"
            size="sm"
            onClick={handleExportAll}
            disabled={isExporting}
          >
            {isExporting ? (
              <Loader2 className="h-3.5 w-3.5 mr-1.5 animate-spin" />
            ) : (
              <Download className="h-3.5 w-3.5 mr-1.5" />
            )}
            Export All
          </Button>
          <Button variant="outline" size="sm" asChild>
            <a href={`/api/repos/${repoId}/export`} download>
              <FolderArchive className="h-3.5 w-3.5 mr-1.5" />
              Download ZIP
            </a>
          </Button>
        </div>
      </div>

      {startHere.length > 0 && (
        <div className="px-4 sm:px-6 pt-3">
          <details className="rounded-lg border border-[var(--color-border-default)] bg-[var(--color-bg-surface)]">
            <summary className="cursor-pointer list-none px-4 py-2.5 text-sm font-medium text-[var(--color-text-primary)] flex items-center gap-2 hover:bg-[var(--color-bg-elevated)] transition-colors">
              <span className="text-[var(--color-accent-primary)]">✨</span>
              Start here
              <span className="text-[10px] font-normal text-[var(--color-text-tertiary)] uppercase tracking-wider">
                first {startHere.length} files to read
              </span>
            </summary>
            <div className="px-3 pb-3 pt-1">
              <FirstFiveFiles
                files={startHere}
                title="Start here"
                hrefFor={(f) => {
                  const pageId = (f as FirstFiveFile & { page_id?: string }).page_id;
                  return pageId
                    ? `/repos/${repoId}/docs?page=${encodeURIComponent(pageId)}`
                    : undefined;
                }}
              />
            </div>
          </details>
        </div>
      )}

      {/* Explorer */}
      <div className="flex-1 min-h-0">
        <DocsExplorer repoId={repoId} />
      </div>
    </div>
  );
}
