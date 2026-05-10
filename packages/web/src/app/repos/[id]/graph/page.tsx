"use client";

import { use, useCallback, useMemo, useState } from "react";
import useSWR from "swr";
import { useQueryState } from "nuqs";
import { useSearchParams } from "next/navigation";
import { GraphFlow } from "@/components/graph/graph-flow";
import { GraphDocPanel } from "@/components/graph/graph-doc-panel";
import { CentralityLeaderboard, type CentralityNode } from "@repowise-dev/ui/graph/centrality-leaderboard";
import { getGraph } from "@/lib/api/graph";
import type { GraphExportResponse } from "@/lib/api/types";

const VALID_VIEW_MODES = new Set(["module", "full", "architecture", "dead", "hotfiles", "unified"]);

export default function GraphPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id: repoId } = use(params);
  const searchParams = useSearchParams();

  const viewModeParam = searchParams.get("viewMode");
  const initialViewMode = VALID_VIEW_MODES.has(viewModeParam ?? "")
    ? (viewModeParam as "module" | "full" | "architecture" | "dead" | "hotfiles" | "unified")
    : undefined;
  const initialNode = searchParams.get("node");

  const [, setSelectedNode] = useQueryState("node");
  const [docNodeId, setDocNodeId] = useState<string | null>(null);

  const { data: graphData } = useSWR<GraphExportResponse>(
    `graph:${repoId}`,
    () => getGraph(repoId),
    { revalidateOnFocus: false, revalidateOnReconnect: false },
  );

  const centralityNodes: CentralityNode[] = useMemo(() => {
    if (!graphData) return [];
    return graphData.nodes.map((n) => ({
      node_id: n.node_id,
      label: n.node_id.split("/").slice(-1)[0],
      pagerank: n.pagerank,
      betweenness: n.betweenness,
      language: n.language,
    }));
  }, [graphData]);

  // Click a file node → open doc panel
  const handleNodeClick = useCallback(
    (nodeId: string, nodeType: string) => {
      // Module clicks are handled inside GraphFlow (drill-down)
      // File clicks open the doc panel
      if (nodeType !== "moduleGroup") {
        setDocNodeId((prev) => (prev === nodeId ? null : nodeId));
        void setSelectedNode(nodeId);
      }
    },
    [setSelectedNode],
  );

  // Double click or context menu "View Docs"
  const handleNodeViewDocs = useCallback(
    (nodeId: string) => {
      setDocNodeId((prev) => (prev === nodeId ? null : nodeId));
      void setSelectedNode(nodeId);
    },
    [setSelectedNode],
  );

  return (
    <div className="flex flex-col h-full">
      {/* Header */}
      <div className="shrink-0 px-4 sm:px-6 py-3 border-b border-[var(--color-border-default)]">
        <h1 className="text-lg font-semibold text-[var(--color-text-primary)]">
          Dependency Graph
        </h1>
        <p className="text-xs text-[var(--color-text-secondary)] mt-0.5">
          Explore dependencies and trace paths between files
        </p>
      </div>

      {/* Graph area */}
      <div className="flex-1 overflow-hidden p-3">
        <div className="h-full w-full rounded-lg border border-[var(--color-border-default)] overflow-hidden relative">
          <GraphFlow
            repoId={repoId}
            initialViewMode={initialNode ? "full" : initialViewMode}
            initialSelectedNode={initialNode}
            onNodeClick={handleNodeClick}
            onNodeViewDocs={handleNodeViewDocs}
          />

          {/* Doc panel — shows on file click */}
          {docNodeId && (
            <GraphDocPanel
              repoId={repoId}
              nodeId={docNodeId}
              onClose={() => setDocNodeId(null)}
            />
          )}

          {/* Centrality leaderboard — collapsible right rail */}
          {centralityNodes.length > 0 && !docNodeId && (
            <div className="absolute top-3 right-3 z-10 max-h-[calc(100%-1.5rem)] flex">
              <CentralityLeaderboard
                nodes={centralityNodes}
                onSelect={(n) => {
                  setDocNodeId(n.node_id);
                  void setSelectedNode(n.node_id);
                }}
              />
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
