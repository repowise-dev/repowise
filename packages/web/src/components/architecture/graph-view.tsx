"use client";

import { useCallback, useState } from "react";
import useSWR from "swr";
import { useQueryState } from "nuqs";
import { useSearchParams } from "next/navigation";
import { GraphFlow } from "@/components/graph/graph-flow";
import { GraphDocPanel } from "@/components/graph/graph-doc-panel";
import { GraphCanvasShell } from "@repowise-dev/ui/graph/graph-canvas-shell";
import { GraphTruncationBanner } from "@repowise-dev/ui/graph/graph-truncation-banner";
import { getGraph } from "@/lib/api/graph";
import type { GraphExportResponse } from "@/lib/api/types";

type ViewMode = "module" | "full" | "architecture" | "dead" | "hotfiles" | "unified";
type ColorMode = "language" | "community" | "risk";

const VALID_COLOR_MODES = new Set<ColorMode>(["language", "community", "risk"]);

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

export function GraphView({
  repoId,
  scope = "map",
  onScopeViewChange,
}: {
  repoId: string;
  /** Which top-level Architecture mode hosts the canvas: Map = constellation,
   *  Explore = file/module graphs. One mounted component serves both. */
  scope?: "map" | "explore";
  /** Scope switches inside the canvas (toolbar) re-sync `?view=` upstream. */
  onScopeViewChange?: (view: "map" | "explore") => void;
}) {
  const searchParams = useSearchParams();

  const viewModeParam = searchParams.get("viewMode");
  const initialViewMode = VALID_VIEW_MODES.has((viewModeParam ?? "") as ViewMode)
    ? (viewModeParam as ViewMode)
    : undefined;
  const initialNode = searchParams.get("node");

  const colorModeParam = searchParams.get("colorMode");
  const initialColorMode = VALID_COLOR_MODES.has((colorModeParam ?? "") as ColorMode)
    ? (colorModeParam as ColorMode)
    : undefined;

  const [, setSelectedNode] = useQueryState("node");
  const [, setColorModeParam] = useQueryState("colorMode");
  const [, setViewModeParam] = useQueryState("viewMode");
  const [docNodeId, setDocNodeId] = useState<string | null>(null);
  const [graphLimit, setGraphLimit] = useState<number | undefined>(undefined);

  // The scope the canvas mounts into: a pinned node forces "full"; an explicit
  // ?viewMode= wins; otherwise the hosting mode decides (Map → constellation,
  // Explore → full graph).
  const mountViewMode: ViewMode = initialNode
    ? "full"
    : initialViewMode ?? (scope === "explore" ? "full" : "architecture");

  // Track the live scope so we only fetch the capped full graph (and render
  // its truncation banner) for scopes that actually use it. Remount the canvas
  // (flowKey) when the truncation banner jumps to the constellation — that is
  // a host-initiated scope change, which GraphFlow only reads at mount.
  const [viewMode, setViewMode] = useState<ViewMode>(mountViewMode);
  const [flowKey, setFlowKey] = useState(0);
  const [forcedViewMode, setForcedViewMode] = useState<ViewMode | null>(null);
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

  // In-canvas scope switches keep the URL honest: `?viewMode=` mirrors the
  // graph scope and `?view=` flips between Map and Explore.
  const handleViewModeChange = useCallback(
    (mode: ViewMode) => {
      setViewMode(mode);
      void setViewModeParam(mode === "architecture" ? null : mode);
      onScopeViewChange?.(mode === "architecture" ? "map" : "explore");
    },
    [onScopeViewChange, setViewModeParam],
  );

  // Color-mode changes (toolbar or 1/2/3 keys) sync to the URL so shared
  // links restore the same coloring.
  const handleColorModeChange = useCallback(
    (mode: ColorMode) => {
      void setColorModeParam(mode);
    },
    [setColorModeParam],
  );

  // "Switch to the Knowledge Graph" from the truncation banner: remount the
  // canvas in the constellation scope. No page reload.
  const handleSwitchToArchitecture = useCallback(() => {
    setForcedViewMode("architecture");
    setFlowKey((k) => k + 1);
    handleViewModeChange("architecture");
  }, [handleViewModeChange]);

  const isMap = scope === "map" && !initialNode;

  return (
    <GraphCanvasShell
      title={isMap ? "Communities" : "Dependency Explorer"}
      description={
        isMap
          ? "Detected communities and how they connect — double-click a hub to blossom it"
          : "Explore dependencies, overlays and trace paths between files"
      }
      banner={
        // Shown only when the current scope renders the capped full graph and
        // the server actually capped it. Constellation / module scopes use
        // their own endpoints, so the banner stays hidden.
        usesFullGraph && graphData?.truncated && graphData.total_node_count != null ? (
          <GraphTruncationBanner
            shown={graphData.nodes.length}
            total={graphData.total_node_count}
            limit={graphLimit ?? graphData.nodes.length}
            onLoadMore={(nextLimit) => setGraphLimit(nextLimit)}
            onSwitchToArchitecture={handleSwitchToArchitecture}
          />
        ) : undefined
      }
      overlay={
        // Doc panel — shows on file click. Single right-rail surface.
        docNodeId ? (
          <GraphDocPanel
            repoId={repoId}
            nodeId={docNodeId}
            onClose={() => setDocNodeId(null)}
          />
        ) : undefined
      }
    >
      <GraphFlow
        key={flowKey}
        repoId={repoId}
        initialViewMode={forcedViewMode ?? mountViewMode}
        // Color mode is controlled here so the URL (?colorMode=) is the single
        // source of truth — back/forward and shared links restore it. Defaults
        // to community coloring, matching the canvas's own default.
        colorMode={initialColorMode ?? "community"}
        initialSelectedNode={initialNode}
        // Communities locks to the constellation; Explore drops the
        // constellation scope (it lives in the Knowledge Graph view) so the
        // toolbar offers only Modules / Full.
        availableScopes={scope === "explore" ? ["modules", "full"] : undefined}
        onNodeClick={handleNodeClick}
        onNodeViewDocs={handleNodeViewDocs}
        onCommunityPanelOpen={handleCommunityPanelOpen}
        onViewModeChange={handleViewModeChange}
        onColorModeChange={handleColorModeChange}
      />
    </GraphCanvasShell>
  );
}
