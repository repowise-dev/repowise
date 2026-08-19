"use client";

import { useCallback, useMemo, useState } from "react";
import useSWR from "swr";
import { useQueryState } from "nuqs";
import { useSearchParams } from "next/navigation";
import { GraphFlow } from "@/components/graph/graph-flow";
import { GraphDocPanel } from "@/components/graph/graph-doc-panel";
import { GraphCanvasShell } from "@repowise-dev/ui/graph/graph-canvas-shell";
import { GraphTruncationBanner } from "@repowise-dev/ui/graph/graph-truncation-banner";
import {
  GraphScopeSwitcher,
  ModuleFilterSelect,
} from "@repowise-dev/ui/graph/graph-scope-controls";
import type { ModuleGroup } from "@repowise-dev/ui/graph/use-module-filter";
import { getGraph } from "@/lib/api/graph";
import type { GraphExportResponse } from "@/lib/api/types";

type ViewMode = "full" | "architecture" | "dead" | "hotfiles" | "unified";
type ColorMode = "language" | "community";
type Scope = "communities" | "files";

// `?colorMode=risk` links predate the removal of that lens; an unlisted value
// falls through to the "community" default rather than erroring.
const VALID_COLOR_MODES = new Set<ColorMode>(["language", "community"]);

/** Scope + signal → the canvas's internal ViewMode. The overlay wins, because
 *  dead/hot are only ever drawn on the file graph. */
function toViewMode(scope: Scope, signal: string | null): ViewMode {
  if (scope === "communities") return "architecture";
  if (signal === "dead") return "dead";
  if (signal === "hot") return "hotfiles";
  return "full";
}

export function GraphView({
  repoId,
  scope,
  onScopeChange,
}: {
  repoId: string;
  /** Controlled by the page, which owns `?view=`. */
  scope: Scope;
  onScopeChange: (scope: Scope) => void;
}) {
  const searchParams = useSearchParams();
  const initialNode = searchParams.get("node");

  const colorModeParam = searchParams.get("colorMode");
  const initialColorMode = VALID_COLOR_MODES.has((colorModeParam ?? "") as ColorMode)
    ? (colorModeParam as ColorMode)
    : undefined;

  const [, setSelectedNode] = useQueryState("node");
  const [, setColorModeParam] = useQueryState("colorMode");
  const [signal, setSignal] = useQueryState("signal");
  const [activeModule, setActiveModule] = useQueryState("module");
  const [docNodeId, setDocNodeId] = useState<string | null>(null);
  const [graphLimit, setGraphLimit] = useState<number | undefined>(undefined);
  const [moduleGroups, setModuleGroups] = useState<ModuleGroup[]>([]);

  // A pinned node is always a file, so a `?node=` link forces the file scope
  // however the URL spells the rest.
  const effectiveScope: Scope = initialNode ? "files" : scope;
  const viewMode = toViewMode(effectiveScope, signal);

  // Only the unfiltered file scope renders the capped `/api/graph` payload.
  // The constellation, and each of the dead/hot signals, has its own endpoint —
  // so neither the fetch nor the truncation banner belongs to them. The banner
  // used to show under the signals too, announcing "1,500 of 3,194 files" over
  // a canvas drawing 734 nodes that came from somewhere else entirely.
  const usesFullGraph = effectiveScope === "files" && !signal;

  const { data: graphData } = useSWR<GraphExportResponse>(
    usesFullGraph ? `graph:${repoId}:${graphLimit ?? "default"}` : null,
    () => getGraph(repoId, graphLimit),
    { revalidateOnFocus: false, revalidateOnReconnect: false },
  );

  // Click a file node → open doc panel
  const handleNodeClick = useCallback(
    (nodeId: string, nodeType: string) => {
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

  // The canvas still owns the dead/hot node filter, and reports it as a
  // ViewMode. Scope changes never arrive this way any more — the switcher in
  // the header drives those — so this only has to keep `?signal=` honest.
  const handleViewModeChange = useCallback(
    (mode: ViewMode) => {
      void setSignal(mode === "dead" ? "dead" : mode === "hotfiles" ? "hot" : null);
    },
    [setSignal],
  );

  const handleColorModeChange = useCallback(
    (mode: ColorMode) => {
      void setColorModeParam(mode);
    },
    [setColorModeParam],
  );

  // "See all of them grouped" from the truncation banner: the whole repo, at
  // the scale where all of it fits.
  const handleSwitchToArchitecture = useCallback(() => {
    onScopeChange("communities");
  }, [onScopeChange]);

  const handleScopeChange = useCallback(
    (next: Scope) => {
      // The module filter is a file-scope concept; carrying it into the
      // communities view would leave a control set to something invisible.
      if (next === "communities") void setActiveModule(null);
      onScopeChange(next);
    },
    [onScopeChange, setActiveModule],
  );

  const isCommunities = effectiveScope === "communities";

  const headerControls = useMemo(
    () => (
      <div className="flex flex-wrap items-center gap-2">
        {!isCommunities && (
          <ModuleFilterSelect
            groups={moduleGroups}
            activeModule={activeModule}
            onModuleChange={(next) => void setActiveModule(next)}
          />
        )}
        <GraphScopeSwitcher scope={effectiveScope} onScopeChange={handleScopeChange} />
      </div>
    ),
    [isCommunities, moduleGroups, activeModule, setActiveModule, effectiveScope, handleScopeChange],
  );

  return (
    <GraphCanvasShell
      // No title. The tab above already says "Map", and the scope switcher on
      // the right says which zoom you are at — a heading that repeats the
      // control you just clicked spends a band of chrome saying nothing. What
      // is left is the one line that adds something neither can: what to do
      // with the thing you are looking at.
      description={
        isCommunities
          ? "Each circle is a detected community, sized by how much code it holds. Double-click one to open it up."
          : "Every file and how it depends on the others. Pick two files to trace a path between them."
      }
      titleActions={headerControls}
      banner={
        // Shown only when the file scope actually got capped. This line is the
        // one place the node count is stated, and it only became true when the
        // export stopped counting symbol nodes as files.
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
        repoId={repoId}
        // Scope is controlled: `?view=` is the single source of truth, so
        // back/forward and shared links restore it without a remount.
        viewMode={viewMode}
        activeModule={activeModule}
        // Same value the banner reports, so the caption and the canvas can
        // never disagree about how many files are drawn.
        graphLimit={graphLimit}
        onModuleGroupsChange={setModuleGroups}
        colorMode={initialColorMode ?? "community"}
        initialSelectedNode={initialNode}
        onNodeClick={handleNodeClick}
        onNodeViewDocs={handleNodeViewDocs}
        onCommunityPanelOpen={handleCommunityPanelOpen}
        onViewModeChange={handleViewModeChange}
        onColorModeChange={handleColorModeChange}
      />
    </GraphCanvasShell>
  );
}
