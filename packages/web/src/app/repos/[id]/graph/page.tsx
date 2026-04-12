"use client";

import { use, useCallback, useState } from "react";
import { useQueryState } from "nuqs";
import { GraphFlow } from "@/components/graph/graph-flow";
import { GraphDocPanel } from "@/components/graph/graph-doc-panel";

export default function GraphPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id: repoId } = use(params);

  const [, setSelectedNode] = useQueryState("node");
  const [docNodeId, setDocNodeId] = useState<string | null>(null);

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
        </div>
      </div>
    </div>
  );
}
