"use client";

import { use, useCallback, useState } from "react";
import useSWR from "swr";
import { useQueryState } from "nuqs";
import { useSearchParams } from "next/navigation";
import { GraphFlow } from "@/components/graph/graph-flow";
import { GraphDocPanel } from "@/components/graph/graph-doc-panel";
import { GraphTruncationBanner } from "@repowise-dev/ui/graph/graph-truncation-banner";
import { getGraph } from "@/lib/api/graph";
import type { GraphExportResponse } from "@/lib/api/types";

type ViewMode = "module" | "full" | "architecture" | "dead" | "hotfiles" | "unified";

const VALID_VIEW_MODES = new Set<ViewMode>([
  "module",
  "full",
  "architecture",
  "dead",
  "hotfiles",
  "unified",
]);

// Scopes that render their own dedicated endpoint and therefore never touch the
// capped full-graph endpoint (`/api/graph`). The constellation ("architecture")
// and the module browser fetch their own graphs, so the page must NOT eagerly
// fetch the full graph — nor show its truncation banner — for these scopes.
const SCOPES_WITHOUT_FULL_GRAPH = new Set<ViewMode>(["architecture", "module"]);

export default function GraphPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id: repoId } = use(params);
  const searchParams = useSearchParams();

  const viewModeParam = searchParams.get("viewMode");
  const initialViewMode = VALID_VIEW_MODES.has((viewModeParam ?? "") as ViewMode)
    ? (viewModeParam as ViewMode)
    : undefined;
  const initialNode = searchParams.get("node");

  const [, setSelectedNode] = useQueryState("node");
  const [docNodeId, setDocNodeId] = useState<string | null>(null);
  const [graphLimit, setGraphLimit] = useState<number | undefined>(undefined);

  // Track the live scope so we only fetch the capped full graph (and render its
  // truncation banner) for scopes that actually use it. Constellation
  // ("architecture") is the default, and — like "module" — fetches its own
  // endpoint inside GraphFlow, so the page must stay off /api/graph there.
  // A pinned node forces the "full" scope (see initialViewMode below).
  const [viewMode, setViewMode] = useState<ViewMode>(
    initialNode ? "full" : initialViewMode ?? "architecture",
  );
  const usesFullGraph = !SCOPES_WITHOUT_FULL_GRAPH.has(viewMode);

  const { data: graphData } = useSWR<GraphExportResponse>(
    usesFullGraph ? `graph:${repoId}:${graphLimit ?? "default"}` : null,
    () => getGraph(repoId, graphLimit),
    { revalidateOnFocus: false, revalidateOnReconnect: false },
  );

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

  // When the community panel opens from the legend, dismiss the doc panel
  // so the two never stack on the right rail. Single-sidebar UX.
  const handleCommunityPanelOpen = useCallback(() => {
    setDocNodeId(null);
  }, []);

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

      {/* Truncation banner — shown only when the current scope renders the
          capped full graph and the server actually capped it. Constellation /
          module scopes use their own endpoints, so the banner stays hidden. */}
      {usesFullGraph && graphData?.truncated && graphData.total_node_count != null && (
        <div className="shrink-0 px-4 sm:px-6 pt-3">
          <GraphTruncationBanner
            shown={graphData.nodes.length}
            total={graphData.total_node_count}
            limit={graphLimit ?? graphData.nodes.length}
            onLoadMore={(nextLimit) => setGraphLimit(nextLimit)}
            onSwitchToArchitecture={() => {
              const url = new URL(window.location.href);
              url.searchParams.set("viewMode", "architecture");
              window.history.replaceState({}, "", url.toString());
              window.location.reload();
            }}
          />
        </div>
      )}

      {/* Graph area */}
      <div className="flex-1 overflow-hidden p-3">
        <div className="h-full w-full rounded-lg border border-[var(--color-border-default)] overflow-hidden relative">
          <GraphFlow
            repoId={repoId}
            initialViewMode={initialNode ? "full" : initialViewMode}
            initialSelectedNode={initialNode}
            onNodeClick={handleNodeClick}
            onNodeViewDocs={handleNodeViewDocs}
            onCommunityPanelOpen={handleCommunityPanelOpen}
            onViewModeChange={setViewMode}
          />

          {/* Doc panel — shows on file click. Single right-rail surface. */}
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
