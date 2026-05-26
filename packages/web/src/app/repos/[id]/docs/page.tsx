"use client";

import { use, useCallback, useMemo, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import useSWR from "swr";
import { Download, FolderArchive, Loader2, Search } from "lucide-react";
import { Button } from "@repowise-dev/ui/ui/button";
import { FirstFiveFiles, type FirstFiveFile } from "@repowise-dev/ui/onboarding/first-five-files";
import { DocsCommandPalette } from "@repowise-dev/ui/docs/command-palette";
import { DocsExplorer } from "@/components/docs/docs-explorer";
import { listAllPages } from "@/lib/api/pages";
import { search as searchPages } from "@/lib/api/search";
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
  const [searchOpen, setSearchOpen] = useState(false);
  const router = useRouter();
  const searchParams = useSearchParams();

  const openPage = useCallback(
    (pageId: string) => {
      const next = new URLSearchParams(searchParams.toString());
      next.set("page", pageId);
      router.replace(`?${next.toString()}`, { scroll: false });
    },
    [router, searchParams],
  );

  const { data: graphData } = useSWR<GraphExportResponse>(
    `graph:${repoId}`,
    () => getGraph(repoId),
    { revalidateOnFocus: false, revalidateOnReconnect: false },
  );

  // Share the exact SWR key the explorer's `usePages` uses so the (large) full
  // page list — content included — is fetched once and deduped, not twice.
  const { data: docPages } = useSWR<DocPage[]>(
    `pages:${repoId}:all`,
    () => listAllPages(repoId) as Promise<DocPage[]>,
    { revalidateOnFocus: false },
  );

  // Server-backed search for the ⌘K palette: hit the semantic/full-text
  // endpoint, then map results back to the loaded page objects so selection
  // behaves identically to a client-side hit. Unknown ids (rare) are dropped.
  const searchFn = useCallback(
    async (q: string) => {
      const results = await searchPages(q, { repo_id: repoId, limit: 30 });
      const byId = new Map((docPages ?? []).map((p) => [p.id, p]));
      return results
        .map((r) => byId.get(r.page_id))
        .filter((p): p is DocPage => p !== undefined);
    },
    [repoId, docPages],
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
          <button
            onClick={() => setSearchOpen(true)}
            className="flex items-center gap-2 rounded-md border border-[var(--color-border-default)] bg-[var(--color-bg-elevated)] px-2.5 py-1.5 text-xs text-[var(--color-text-tertiary)] transition-colors hover:text-[var(--color-text-secondary)]"
          >
            <Search className="h-3.5 w-3.5" />
            <span className="hidden sm:inline">Search</span>
            <kbd className="hidden rounded border border-[var(--color-border-default)] px-1 py-0.5 text-[10px] sm:inline">
              ⌘K
            </kbd>
          </button>
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
          <FirstFiveFiles
            files={startHere}
            title="Start here"
            collapsible
            defaultCollapsed
            hrefFor={(f) => {
              const pageId = (f as FirstFiveFile & { page_id?: string }).page_id;
              return pageId
                ? `/repos/${repoId}/docs?page=${encodeURIComponent(pageId)}`
                : undefined;
            }}
          />
        </div>
      )}

      {/* Explorer */}
      <div className="flex-1 min-h-0">
        <DocsExplorer repoId={repoId} />
      </div>

      {/* ⌘K full-text command palette over loaded pages */}
      <DocsCommandPalette
        pages={docPages ?? []}
        open={searchOpen}
        onOpenChange={setSearchOpen}
        onSelect={(p) => openPage(p.id)}
        searchFn={searchFn}
      />
    </div>
  );
}
