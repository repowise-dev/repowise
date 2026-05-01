"use client";

import {
  createContext,
  useCallback,
  useMemo,
  useState,
  useEffect,
  useRef,
} from "react";
import {
  ReactFlow,
  MiniMap,
  Controls,
  Background,
  BackgroundVariant,
  ReactFlowProvider,
  useReactFlow,
  type NodeMouseHandler,
  type Node,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { Loader2, ChevronRight, Home, Search } from "lucide-react";
import Fuse from "fuse.js";
import { Skeleton } from "@/components/ui/skeleton";
import { Input } from "@/components/ui/input";
import { EmptyState } from "@/components/shared/empty-state";
import {
  useModuleGraph,
  useGraph,
  useArchitectureGraph,
  useDeadCodeGraph,
  useHotFilesGraph,
  useCommunities,
  useExecutionFlows,
} from "@/lib/hooks/use-graph";
import { ModuleGroupNode } from "./nodes/module-group-node";
import { FileNode } from "./nodes/file-node";
import { DependencyEdge } from "./edges/dependency-edge";
import { groupNodesAsModules, type FileNodeData } from "./elk-layout";
import { useModuleElkLayout, useFileElkLayout } from "./use-elk-layout";
import { PathFinderPanel } from "./path-finder-panel";
import { GraphToolbar, type ColorMode, type ViewMode } from "./graph-toolbar";
import { GraphLegend } from "./graph-legend";
import { GraphCommunityPanel } from "./graph-community-panel";
import { GraphContextMenu } from "./graph-context-menu";
import { GraphTooltip } from "./graph-tooltip";
import { languageColor } from "@/lib/utils/confidence";

// ---- Context ----

export interface GraphContextValue {
  highlightedPath: Set<string>;
  highlightedEdges: Set<string>;
  colorMode: ColorMode;
  riskScores: Map<string, number>;
  hoveredNodeId: string | null;
  connectedNodeIds: Set<string>;
  connectedEdgeIds: Set<string>;
  selectedNodeId: string | null;
  searchDimmedNodes: Set<string> | null;
}

export const GraphContext = createContext<GraphContextValue>({
  highlightedPath: new Set(),
  highlightedEdges: new Set(),
  colorMode: "language",
  riskScores: new Map(),
  hoveredNodeId: null,
  connectedNodeIds: new Set(),
  connectedEdgeIds: new Set(),
  selectedNodeId: null,
  searchDimmedNodes: null,
});

// ---- Node/Edge types ----

const nodeTypes = {
  moduleGroup: ModuleGroupNode,
  fileNode: FileNode,
};

const edgeTypes = {
  dependency: DependencyEdge,
};

// ---- Inner component (needs ReactFlowProvider) ----

interface GraphFlowInnerProps {
  repoId: string;
  onNodeClick?: (nodeId: string, nodeType: string) => void | Promise<void>;
  onNodeViewDocs?: (nodeId: string) => void;
}

function GraphFlowInner({
  repoId,
  onNodeClick,
  onNodeViewDocs,
}: GraphFlowInnerProps) {
  const reactFlow = useReactFlow();

  // State — read initial colorMode from URL if present
  const [viewMode, setViewMode] = useState<ViewMode>("module");
  const [colorMode, setColorMode] = useState<ColorMode>(() => {
    if (typeof window !== "undefined") {
      const params = new URLSearchParams(window.location.search);
      const cm = params.get("colorMode");
      if (cm === "community" || cm === "language" || cm === "risk") return cm;
    }
    return "language";
  });
  const [hideTests, setHideTests] = useState(false);
  const [highlightedPath, setHighlightedPath] = useState<Set<string>>(new Set());
  const [highlightedEdges, setHighlightedEdges] = useState<Set<string>>(new Set());
  const [showPathFinder, setShowPathFinder] = useState(false);

  // Drill-down: stack of module prefixes. e.g. ["packages", "packages/web"]
  const [modulePath, setModulePath] = useState<string[]>([]);
  const currentPrefix = modulePath.length > 0 ? modulePath[modulePath.length - 1] : "";
  const isDrilledDown = modulePath.length > 0;

  // Context menu
  const [ctxMenu, setCtxMenu] = useState<{
    x: number; y: number; nodeId: string; nodeType: string;
  } | null>(null);

  // Path finder pre-fill
  const [pathFrom, setPathFrom] = useState("");
  const [pathTo, setPathTo] = useState("");

  // Hover & selection tracking
  const [hoveredNodeId, setHoveredNodeId] = useState<string | null>(null);
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);
  const [selectedNodeScreen, setSelectedNodeScreen] = useState<{ x: number; y: number } | null>(null);

  // Graph search
  const [searchQuery, setSearchQuery] = useState("");
  const [searchDimmedNodes, setSearchDimmedNodes] = useState<Set<string> | null>(null);

  // Execution flow highlighting
  const [activeFlowIdx, setActiveFlowIdx] = useState<number | null>(null);
  const [showFlows, setShowFlows] = useState(false);

  // ---- Data fetching ----
  const isModuleView = viewMode === "module";

  // Top-level module graph (only when at root of module view)
  const { graph: moduleGraph, isLoading: moduleLoading } = useModuleGraph(
    isModuleView && !isDrilledDown ? repoId : null,
  );

  // Full graph — needed for drill-down and other views
  const needsFullGraph = isDrilledDown || viewMode === "full";
  const { graph: fullGraph, isLoading: fullLoading } = useGraph(
    needsFullGraph ? repoId : null,
  );

  const { graph: archGraph, isLoading: archLoading } = useArchitectureGraph(
    viewMode === "architecture" ? repoId : null,
  );
  const { graph: deadGraph, isLoading: deadLoading } = useDeadCodeGraph(
    viewMode === "dead" ? repoId : null,
  );
  const { graph: hotGraph, isLoading: hotLoading } = useHotFilesGraph(
    viewMode === "hotfiles" ? repoId : null,
  );

  // Community data for legend labels and detail panel
  const { communities } = useCommunities(repoId);
  const communityLabels = useMemo(() => {
    if (!communities) return undefined;
    const m = new Map<number, string>();
    for (const c of communities) m.set(c.community_id, c.label);
    return m;
  }, [communities]);
  const [communityPanelId, setCommunityPanelId] = useState<number | null>(null);

  // Execution flows data
  const { flows: executionFlowsData } = useExecutionFlows(repoId, { top_n: 10, max_depth: 6 });

  // ---- Derived data ----

  // When drilled down, re-group nodes as sub-modules at the current prefix
  const drillDownData = useMemo(() => {
    if (!isDrilledDown || !fullGraph) return null;
    return groupNodesAsModules(fullGraph.nodes, fullGraph.links, currentPrefix);
  }, [isDrilledDown, fullGraph, currentPrefix]);

  // File-level graph data — only for non-module views
  const fileGraphData = useMemo(() => {
    switch (viewMode) {
      case "full":
        return fullGraph ? { nodes: fullGraph.nodes, links: fullGraph.links } : undefined;
      case "architecture":
        return archGraph ? { nodes: archGraph.nodes, links: archGraph.links } : undefined;
      case "dead":
        return deadGraph ? { nodes: deadGraph.nodes, links: deadGraph.links } : undefined;
      case "hotfiles":
        return hotGraph ? { nodes: hotGraph.nodes, links: hotGraph.links } : undefined;
      default:
        return undefined;
    }
  }, [viewMode, fullGraph, archGraph, deadGraph, hotGraph]);

  // ---- Layout selection ----
  // Module view (including drill-down) always uses module layout
  // Other views use file layout

  const moduleLayoutInput = useMemo(() => {
    if (!isModuleView) return { nodes: undefined, edges: undefined, fileEntries: undefined };
    if (!isDrilledDown) {
      return { nodes: moduleGraph?.nodes, edges: moduleGraph?.edges, fileEntries: undefined };
    }
    return {
      nodes: drillDownData?.moduleNodes,
      edges: drillDownData?.moduleEdges,
      fileEntries: drillDownData?.fileEntries,
    };
  }, [isModuleView, isDrilledDown, moduleGraph, drillDownData]);

  const {
    nodes: moduleNodes,
    edges: moduleEdges,
    isLayouting: moduleLayouting,
  } = useModuleElkLayout(moduleLayoutInput.nodes, moduleLayoutInput.edges, moduleLayoutInput.fileEntries);

  const {
    nodes: fileNodes,
    edges: fileEdges,
    isLayouting: fileLayouting,
  } = useFileElkLayout(
    !isModuleView ? fileGraphData?.nodes : undefined,
    !isModuleView ? fileGraphData?.links : undefined,
  );

  const currentNodes = isModuleView ? moduleNodes : fileNodes;
  const currentEdges = isModuleView ? moduleEdges : fileEdges;

  // Filter hidden tests
  const filteredNodes = useMemo(() => {
    if (!hideTests) return currentNodes;
    return currentNodes.filter((n) => {
      if (n.type === "fileNode") {
        return !(n.data as { isTest?: boolean }).isTest;
      }
      return true;
    });
  }, [currentNodes, hideTests]);

  const isLoading =
    (isModuleView && !isDrilledDown) ? moduleLoading :
    isDrilledDown ? fullLoading :
    viewMode === "full" ? fullLoading :
    viewMode === "architecture" ? archLoading :
    viewMode === "dead" ? deadLoading :
    viewMode === "hotfiles" ? hotLoading : false;
  const isLayouting = isModuleView ? moduleLayouting : fileLayouting;

  // Compute connected nodes/edges for hover highlighting
  const { connectedNodeIds, connectedEdgeIds } = useMemo(() => {
    if (!hoveredNodeId) return { connectedNodeIds: new Set<string>(), connectedEdgeIds: new Set<string>() };
    const nodeIds = new Set<string>([hoveredNodeId]);
    const edgeIds = new Set<string>();
    for (const edge of currentEdges) {
      if (edge.source === hoveredNodeId || edge.target === hoveredNodeId) {
        edgeIds.add(edge.id);
        nodeIds.add(edge.source);
        nodeIds.add(edge.target);
      }
    }
    return { connectedNodeIds: nodeIds, connectedEdgeIds: edgeIds };
  }, [hoveredNodeId, currentEdges]);

  // Fuse search index over visible nodes
  const fuseIndex = useMemo(() => {
    const items = filteredNodes.map((n) => ({ id: n.id, label: (n.data as { label?: string }).label ?? n.id }));
    return new Fuse(items, { keys: ["id", "label"], threshold: 0.4 });
  }, [filteredNodes]);

  // Search: compute dimmed set when query is active
  useEffect(() => {
    if (!searchQuery || searchQuery.length < 2) {
      setSearchDimmedNodes(null);
      return;
    }
    const results = fuseIndex.search(searchQuery);
    const matchIds = new Set(results.map((r) => r.item.id));
    const dimmed = new Set<string>();
    for (const n of filteredNodes) {
      if (!matchIds.has(n.id)) dimmed.add(n.id);
    }
    setSearchDimmedNodes(dimmed);

    // Zoom to matched nodes
    if (results.length > 0 && results.length <= 20) {
      const matchedRfNodes = results
        .map((r) => reactFlow.getNode(r.item.id))
        .filter(Boolean) as Node[];
      if (matchedRfNodes.length > 0) {
        reactFlow.fitView({ nodes: matchedRfNodes, padding: 0.4, duration: 500 });
      }
    }
  }, [searchQuery, fuseIndex, filteredNodes, reactFlow]);

  // Execution flow highlighting
  useEffect(() => {
    if (activeFlowIdx === null || !executionFlowsData) {
      // Don't clear if a manual path is highlighted
      if (activeFlowIdx === null && showFlows) {
        setHighlightedPath(new Set());
        setHighlightedEdges(new Set());
      }
      return;
    }
    const flow = executionFlowsData.flows[activeFlowIdx];
    if (!flow) return;
    const pathSet = new Set(flow.trace);
    const edgeKeys = new Set<string>();
    for (let i = 0; i < flow.trace.length - 1; i++) {
      edgeKeys.add(`${flow.trace[i]}→${flow.trace[i + 1]}`);
      edgeKeys.add(`${flow.trace[i + 1]}→${flow.trace[i]}`);
    }
    setHighlightedPath(pathSet);
    setHighlightedEdges(edgeKeys);

    // Switch to full view if in module view to show the trace
    if (viewMode === "module") {
      setViewMode("full");
      setModulePath([]);
    }

    setTimeout(() => {
      const traceNodes = flow.trace
        .map((id) => reactFlow.getNode(id))
        .filter(Boolean) as Node[];
      if (traceNodes.length > 0) {
        reactFlow.fitView({ nodes: traceNodes, padding: 0.3, duration: 600 });
      }
    }, 800);
  }, [activeFlowIdx, executionFlowsData]);

  // Context value
  const ctxValue = useMemo<GraphContextValue>(
    () => ({
      highlightedPath,
      highlightedEdges,
      colorMode,
      riskScores: new Map(),
      hoveredNodeId,
      connectedNodeIds,
      connectedEdgeIds,
      selectedNodeId,
      searchDimmedNodes,
    }),
    [highlightedPath, highlightedEdges, colorMode, hoveredNodeId, connectedNodeIds, connectedEdgeIds, selectedNodeId, searchDimmedNodes],
  );

  // ---- Handlers ----

  const handleNodeClick: NodeMouseHandler = useCallback(
    (event, node) => {
      if (node.type === "moduleGroup" && isModuleView) {
        // Drill into this module — the node ID is the full module path
        setModulePath((prev) => [...prev, node.id]);
        setSelectedNodeId(null);
        return;
      }
      // In community mode, open community detail panel
      if (colorMode === "community" && node.type === "fileNode") {
        const nodeData = node.data as FileNodeData;
        if (nodeData?.communityId !== undefined) {
          setCommunityPanelId(nodeData.communityId);
        }
      }

      // Toggle selection — show tooltip on click
      const mEvent = event as unknown as React.MouseEvent;
      if (selectedNodeId === node.id) {
        setSelectedNodeId(null);
        setSelectedNodeScreen(null);
      } else {
        setSelectedNodeId(node.id);
        setSelectedNodeScreen({ x: mEvent.clientX, y: mEvent.clientY });
      }
    },
    [isModuleView, selectedNodeId, colorMode],
  );

  const handleNodeMouseEnter: NodeMouseHandler = useCallback(
    (_event, node) => { setHoveredNodeId(node.id); },
    [],
  );

  const handleNodeMouseLeave: NodeMouseHandler = useCallback(
    () => { setHoveredNodeId(null); },
    [],
  );

  const handleNodeDoubleClick: NodeMouseHandler = useCallback(
    (_event, node) => {
      if (node.type !== "moduleGroup") {
        onNodeViewDocs?.(node.id);
      }
    },
    [onNodeViewDocs],
  );

  const handleNodeContextMenu: NodeMouseHandler = useCallback(
    (event, node) => {
      event.preventDefault();
      setCtxMenu({
        x: (event as unknown as React.MouseEvent).clientX,
        y: (event as unknown as React.MouseEvent).clientY,
        nodeId: node.id,
        nodeType: node.type ?? "fileNode",
      });
    },
    [],
  );

  useEffect(() => {
    if (!ctxMenu) return;
    const close = () => setCtxMenu(null);
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") close();
    };
    window.addEventListener("click", close);
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("click", close);
      window.removeEventListener("keydown", onKey);
    };
  }, [ctxMenu]);

  const handlePathFound = useCallback(
    (pathNodes: string[]) => {
      setHighlightedPath(new Set(pathNodes));
      const edgeKeys = new Set<string>();
      for (let i = 0; i < pathNodes.length - 1; i++) {
        edgeKeys.add(`${pathNodes[i]}→${pathNodes[i + 1]}`);
        edgeKeys.add(`${pathNodes[i + 1]}→${pathNodes[i]}`);
      }
      setHighlightedEdges(edgeKeys);
      if (viewMode === "module") {
        setViewMode("full");
        setModulePath([]);
      }
      setTimeout(() => {
        const pathRfNodes = pathNodes
          .map((id) => reactFlow.getNode(id))
          .filter(Boolean) as Node[];
        if (pathRfNodes.length > 0) {
          reactFlow.fitView({ nodes: pathRfNodes, padding: 0.3, duration: 600 });
        }
      }, 800);
    },
    [viewMode, reactFlow],
  );

  const handlePathClear = useCallback(() => {
    setHighlightedPath(new Set());
    setHighlightedEdges(new Set());
  }, []);

  const handleFitView = useCallback(() => {
    reactFlow.fitView({ padding: 0.15, duration: 400 });
  }, [reactFlow]);

  const handleViewChange = useCallback((v: ViewMode) => {
    setViewMode(v);
    setModulePath([]);
    setHighlightedPath(new Set());
    setHighlightedEdges(new Set());
    setSelectedNodeId(null);
    setSelectedNodeScreen(null);
  }, []);

  // Breadcrumb
  const handleBreadcrumbClick = useCallback((index: number) => {
    if (index < 0) {
      setModulePath([]);
    } else {
      setModulePath((prev) => prev.slice(0, index + 1));
    }
  }, []);

  // Context menu actions
  const handleCtxViewDocs = useCallback(() => {
    if (ctxMenu) onNodeViewDocs?.(ctxMenu.nodeId);
    setCtxMenu(null);
  }, [ctxMenu, onNodeViewDocs]);

  const handleCtxExplore = useCallback(() => {
    if (ctxMenu) {
      if (ctxMenu.nodeType === "moduleGroup" && isModuleView) {
        setModulePath((prev) => [...prev, ctxMenu.nodeId]);
      } else {
        onNodeClick?.(ctxMenu.nodeId, ctxMenu.nodeType);
      }
    }
    setCtxMenu(null);
  }, [ctxMenu, isModuleView, onNodeClick]);

  const handleCtxPathFrom = useCallback(() => {
    if (ctxMenu) { setPathFrom(ctxMenu.nodeId); setShowPathFinder(true); }
    setCtxMenu(null);
  }, [ctxMenu]);

  const handleCtxPathTo = useCallback(() => {
    if (ctxMenu) { setPathTo(ctxMenu.nodeId); setShowPathFinder(true); }
    setCtxMenu(null);
  }, [ctxMenu]);

  // After layout completes, zoom to entry-point nodes for a readable first view
  const hasFocusedRef = useRef(false);

  useEffect(() => {
    // Only auto-focus once per view mode, after layout is done and nodes exist
    if (isLayouting || filteredNodes.length === 0 || hasFocusedRef.current) return;
    hasFocusedRef.current = true;

    const timer = setTimeout(() => {
      // Per-view zoom strategy
      switch (viewMode) {
        case "dead":
        case "hotfiles":
        case "architecture": {
          // Small, focused graphs — fit all nodes, zoom in to read them
          reactFlow.fitView({ padding: 0.3, duration: 600, maxZoom: 1.5 });
          return;
        }
        case "module": {
          // Module view — fit all, comfortable zoom
          reactFlow.fitView({ padding: 0.2, duration: 600, maxZoom: 1 });
          return;
        }
        default:
          break;
      }

      // Full graph: zoom to show all nodes but cap zoom so they're readable
      reactFlow.fitView({ padding: 0.15, duration: 600, maxZoom: 0.6 });
    }, 100);

    return () => clearTimeout(timer);
  }, [isLayouting, filteredNodes, reactFlow]);

  // Reset focus flag when view mode changes
  useEffect(() => {
    hasFocusedRef.current = false;
  }, [viewMode]);

  const minimapNodeColor = useCallback(
    (node: Node) => {
      if (node.type === "moduleGroup") {
        const pct = (node.data as { docCoveragePct?: number }).docCoveragePct;
        if (pct != null) return pct >= 0.7 ? "#22c55e" : pct >= 0.3 ? "#f59520" : "#ef4444";
        return "#3b82f6";
      }
      const lang = (node.data as { language?: string }).language;
      return lang ? languageColor(lang) : "#8b5cf6";
    },
    [],
  );

  if (isLoading) return <Skeleton className="h-full w-full rounded-lg" />;

  return (
    <GraphContext.Provider value={ctxValue}>
      <div className="relative w-full h-full" style={{ touchAction: "none" }} aria-label="Dependency graph">
        {isLayouting && (
          <div className="absolute inset-0 z-50 flex items-center justify-center bg-[var(--color-bg-root)]/60 backdrop-blur-sm rounded-lg">
            <div className="flex items-center gap-3 text-sm text-[var(--color-text-secondary)]">
              <Loader2 className="w-5 h-5 animate-spin text-[var(--color-accent-graph)]" />
              Computing layout...
            </div>
          </div>
        )}

        {filteredNodes.length === 0 && !isLayouting ? (
          <div className="flex items-center justify-center h-full">
            <EmptyState
              title="No graph data"
              description="Check that the backend is running and this repo has been indexed."
            />
          </div>
        ) : (
        <ReactFlow
          nodes={filteredNodes}
          edges={currentEdges}
          nodeTypes={nodeTypes}
          edgeTypes={edgeTypes}
          onNodeClick={handleNodeClick}
          onNodeDoubleClick={handleNodeDoubleClick}
          onNodeContextMenu={handleNodeContextMenu}
          onNodeMouseEnter={handleNodeMouseEnter}
          onNodeMouseLeave={handleNodeMouseLeave}
          onPaneClick={() => { setSelectedNodeId(null); setSelectedNodeScreen(null); }}
          fitView
          fitViewOptions={{ padding: 0.3, maxZoom: 1.2 }}
          minZoom={0.05}
          maxZoom={4}
          proOptions={{ hideAttribution: true }}
          className="!bg-transparent"
          nodesDraggable={false}
          defaultEdgeOptions={{ type: "dependency" }}
        >
          <Background variant={BackgroundVariant.Dots} gap={16} size={0.5} color="rgba(255,255,255,0.03)" />
          <Controls
            showInteractive={false}
            className="!border-[var(--color-border-default)] !bg-[var(--color-bg-elevated)] !shadow-lg !shadow-black/20 [&>button]:!border-[var(--color-border-default)] [&>button]:!bg-[var(--color-bg-elevated)] [&>button]:!text-[var(--color-text-secondary)] [&>button:hover]:!bg-[var(--color-bg-overlay)] [&>button:hover]:!text-[var(--color-text-primary)]"
          />
          <MiniMap
            nodeColor={minimapNodeColor}
            maskColor="rgba(0, 0, 0, 0.6)"
            className="!bg-[var(--color-bg-surface)] !border-[var(--color-border-default)] !shadow-lg !shadow-black/20 !rounded-lg !hidden sm:!block"
            pannable
            zoomable
          />
        </ReactFlow>
        )}

        {/* Breadcrumb — shown when drilled into a module */}
        {isModuleView && isDrilledDown && (
          <div className="absolute top-3 left-3 z-10">
            <div className="flex items-center gap-1 rounded-lg border border-[var(--color-border-default)] bg-[var(--color-bg-elevated)]/90 backdrop-blur-sm px-2.5 py-1.5 shadow-lg shadow-black/20">
              <button
                onClick={() => handleBreadcrumbClick(-1)}
                className="flex items-center gap-1 text-[11px] text-[var(--color-text-tertiary)] hover:text-[var(--color-accent-graph)] transition-colors"
              >
                <Home className="w-3 h-3" />
                <span>Root</span>
              </button>
              {modulePath.map((fullPrefix, i) => {
                // Show only the segment added at this level
                const prevPrefix = i > 0 ? modulePath[i - 1] + "/" : "";
                const label = fullPrefix.slice(prevPrefix.length);
                const isLast = i === modulePath.length - 1;
                return (
                  <span key={i} className="flex items-center gap-1">
                    <ChevronRight className="w-3 h-3 text-[var(--color-text-tertiary)]" />
                    <button
                      onClick={() => !isLast && handleBreadcrumbClick(i)}
                      className={`text-[11px] font-mono transition-colors ${
                        isLast
                          ? "text-[var(--color-text-primary)] font-medium cursor-default"
                          : "text-[var(--color-text-tertiary)] hover:text-[var(--color-accent-graph)]"
                      }`}
                    >
                      {label}
                    </button>
                  </span>
                );
              })}
            </div>
          </div>
        )}

        {/* Toolbar */}
        <div className="absolute top-3 right-3 z-10">
          <GraphToolbar
            viewMode={viewMode}
            onViewChange={handleViewChange}
            colorMode={colorMode}
            onColorModeChange={setColorMode}
            hideTests={hideTests}
            onHideTestsChange={setHideTests}
            onFitView={handleFitView}
            showPathFinder={showPathFinder}
            onTogglePathFinder={() => setShowPathFinder((s) => !s)}
            showFlows={showFlows}
            onToggleFlows={() => { setShowFlows((s) => !s); setActiveFlowIdx(null); }}
            searchQuery={searchQuery}
            onSearchChange={setSearchQuery}
          />
        </div>

        {/* Path Finder */}
        {showPathFinder && (
          <div className="absolute top-14 right-3 z-10">
            <PathFinderPanel
              repoId={repoId}
              onPathFound={handlePathFound}
              onClear={handlePathClear}
              onClose={() => setShowPathFinder(false)}
              initialFrom={pathFrom}
              initialTo={pathTo}
            />
          </div>
        )}

        {/* Execution Flows Panel */}
        {showFlows && executionFlowsData && executionFlowsData.flows.length > 0 && (
          <div className="absolute top-14 right-3 z-10 w-[min(16rem,calc(100vw-1.5rem))]">
            <div className="rounded-lg border border-[var(--color-border-default)] bg-[var(--color-bg-elevated)]/95 backdrop-blur-sm shadow-lg shadow-black/20 p-3">
              <div className="flex items-center justify-between mb-2">
                <span className="text-[11px] font-medium text-[var(--color-text-primary)]">
                  Execution Flows
                </span>
                <span className="text-[10px] text-[var(--color-text-tertiary)]">
                  {executionFlowsData.flows.length} entry points
                </span>
              </div>
              <div className="space-y-1 max-h-60 overflow-y-auto">
                {executionFlowsData.flows.map((flow, idx) => (
                  <button
                    key={flow.entry_point}
                    onClick={() => setActiveFlowIdx(activeFlowIdx === idx ? null : idx)}
                    className={`w-full text-left px-2 py-1.5 rounded-md text-[11px] transition-colors ${
                      activeFlowIdx === idx
                        ? "bg-[var(--color-accent-primary)]/15 text-[var(--color-accent-primary)]"
                        : "text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-overlay)] hover:text-[var(--color-text-primary)]"
                    }`}
                  >
                    <div className="font-mono truncate">{flow.entry_point_name}</div>
                    <div className="flex items-center gap-2 mt-0.5 text-[10px] text-[var(--color-text-tertiary)]">
                      <span>depth {flow.depth}</span>
                      <span>{flow.trace.length} nodes</span>
                      {flow.crosses_community && (
                        <span className="text-yellow-500">cross-community</span>
                      )}
                    </div>
                  </button>
                ))}
              </div>
            </div>
          </div>
        )}

        {/* Legend */}
        <div className="absolute bottom-3 left-3 z-10">
          <GraphLegend
            nodeCount={filteredNodes.length}
            edgeCount={currentEdges.length}
            colorMode={colorMode}
            viewMode={viewMode}
            communityLabels={communityLabels}
          />
        </div>

        {/* Context menu */}
        {ctxMenu && (
          <GraphContextMenu
            x={ctxMenu.x}
            y={ctxMenu.y}
            nodeId={ctxMenu.nodeId}
            isModule={ctxMenu.nodeType === "moduleGroup"}
            onViewDocs={handleCtxViewDocs}
            onExplore={handleCtxExplore}
            onPathFrom={handleCtxPathFrom}
            onPathTo={handleCtxPathTo}
          />
        )}

        {/* Community detail panel */}
        {communityPanelId !== null && (
          <GraphCommunityPanel
            repoId={repoId}
            communityId={communityPanelId}
            onClose={() => setCommunityPanelId(null)}
          />
        )}

        {/* Node detail tooltip */}
        {selectedNodeId && selectedNodeScreen && (() => {
          const rfNode = filteredNodes.find((n) => n.id === selectedNodeId);
          if (!rfNode) return null;
          return (
            <GraphTooltip
              nodeId={selectedNodeId}
              nodeType={rfNode.type ?? "fileNode"}
              data={rfNode.data as Record<string, unknown>}
              x={selectedNodeScreen.x}
              y={selectedNodeScreen.y}
              onClose={() => { setSelectedNodeId(null); setSelectedNodeScreen(null); }}
              onViewDocs={() => { onNodeViewDocs?.(selectedNodeId); setSelectedNodeId(null); setSelectedNodeScreen(null); }}
              onExplore={rfNode.type === "moduleGroup" && isModuleView ? () => {
                setModulePath((prev) => [...prev, selectedNodeId]);
                setSelectedNodeId(null);
                setSelectedNodeScreen(null);
              } : undefined}
            />
          );
        })()}
      </div>
    </GraphContext.Provider>
  );
}

// ---- Public component ----

export interface GraphFlowProps {
  repoId: string;
  onNodeClick?: (nodeId: string, nodeType: string) => void | Promise<void>;
  onNodeViewDocs?: (nodeId: string) => void;
}

export function GraphFlow(props: GraphFlowProps) {
  return (
    <ReactFlowProvider>
      <GraphFlowInner {...props} />
    </ReactFlowProvider>
  );
}
