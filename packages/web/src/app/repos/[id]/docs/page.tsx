"use client";

import { use, useMemo, useState } from "react";
import useSWR from "swr";
import { Download, FolderArchive, Loader2 } from "lucide-react";
import { Button } from "@repowise-dev/ui/ui/button";
import { FirstFiveFiles, type FirstFiveFile } from "@repowise-dev/ui/onboarding/first-five-files";
import { DocsExplorer } from "@/components/docs/docs-explorer";
import { listAllPages } from "@/lib/api/pages";
import { getGraph } from "@/lib/api/graph";
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

  const startHere: FirstFiveFile[] = useMemo(() => {
    if (!graphData) return [];
    const entries = graphData.nodes.filter((n) => n.is_entry_point);
    const pool = entries.length > 0 ? entries : graphData.nodes;
    return [...pool]
      .sort((a, b) => b.pagerank - a.pagerank)
      .slice(0, 5)
      .map((n) => ({
        file_path: n.node_id,
        is_entry_point: n.is_entry_point,
        has_doc: n.has_doc,
        pagerank: n.pagerank,
        reason: n.is_entry_point ? "entry point" : "high centrality",
      }));
  }, [graphData]);

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
          <FirstFiveFiles
            files={startHere}
            title="Start here"
            hrefFor={(f) => `/repos/${repoId}/wiki/${encodeURIComponent(f.file_path)}`}
          />
        </div>
      )}

      {/* Explorer */}
      <div className="flex-1 min-h-0">
        <DocsExplorer repoId={repoId} />
      </div>
    </div>
  );
}
