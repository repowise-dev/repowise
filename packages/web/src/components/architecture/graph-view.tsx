"use client";

import { useCallback, useMemo, useState } from "react";
import useSWR from "swr";
import { useQueryState } from "nuqs";
import { useSearchParams } from "next/navigation";
import { GraphFlow } from "@/components/graph/graph-flow";
import { GraphDocPanel } from "@/components/graph/graph-doc-panel";
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

  // The rail holds one panel, and the doc panel outranks the inspector, so a
  // doc left open would mask every later selection. Drop it once the selection
  // moves elsewhere.
  const handleSelectedNodeChange = useCallback((nodeId: string | null) => {
    setDocNodeId((prev) => (prev !== null && prev !== nodeId ? null : prev));
  }, []);

  // Opening a community also replaces the doc panel.
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
    <GraphFlow
      repoId={repoId}
      // No title. The tab above says "Map" and the scope switcher says which
      // zoom you are at; the one line left is what to do with what you see.
      description={
        isCommunities
          ? "Files that depend on each other more than on the rest of the repo, detected automatically. Circle size is how much code a group holds, and the nearer the centre, the nearer an entry point. Double-click a group to see the files inside it and what they depend on."
          : "Every file and how it depends on the others. Pick two files to trace a path between them."
      }
      headerActions={headerControls}
      banner={
        // Only when the file scope actually got capped.
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
      rail={
        docNodeId ? (
          <GraphDocPanel
            repoId={repoId}
            nodeId={docNodeId}
            onClose={() => setDocNodeId(null)}
          />
        ) : undefined
      }
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
      onSelectedNodeChange={handleSelectedNodeChange}
      onViewModeChange={handleViewModeChange}
      onColorModeChange={handleColorModeChange}
    />
  );
}
