"use client";

import {
  useCallback,
  useMemo,
  useState,
  useEffect,
  useRef,
  type ReactNode,
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
  type Edge,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { Loader2, ChevronRight, Home } from "lucide-react";
import Fuse from "fuse.js";
import { Skeleton } from "../ui/skeleton";
import { EmptyState } from "../shared/empty-state";
import { ModuleGroupNode } from "./nodes/module-group-node";
import { FileNode } from "./nodes/file-node";
import { DependencyEdge } from "./edges/dependency-edge";
import { GraphProvider, type GraphContextValue } from "./context";
import { GraphTooltip } from "./graph-tooltip";
import { groupNodesAsModules, type FileNodeData } from "./elk-layout";
import { computeForceLayout } from "./force-layout";
import { useModuleElkLayout, useFileElkLayout } from "./use-elk-layout";
import { GraphToolbar, type ColorMode, type ViewMode, type LayoutMode, type GraphTheme } from "./graph-toolbar";
import { GraphLegend } from "./graph-legend";
import { GraphContextMenu } from "./graph-context-menu";
import { GraphInspectionPanel } from "./graph-inspection-panel";
import { languageColor } from "../lib/confidence";
import type {
  GraphExport,
  ModuleGraph,
  ExecutionFlows,
  CommunitySummaryItem,
} from "@repowise-dev/types/graph";

// ---- Node/Edge types ----

const nodeTypes = {
  moduleGroup: ModuleGroupNode,
  fileNode: FileNode,
};

const edgeTypes = {
  dependency: DependencyEdge,
};

// ---- Props ----

export interface GraphFlowProps {
  /** Top-level module rollup; only fetched/needed for the module view at root. */
  moduleGraph: ModuleGraph | undefined;
  isLoadingModuleGraph: boolean;

  /** Full file-level graph; needed for drill-down and the "full" view. */
  fullGraph: GraphExport | undefined;
  isLoadingFullGraph: boolean;

  /** Architecture view graph. */
  architectureGraph: GraphExport | undefined;
  isLoadingArchitectureGraph: boolean;

  /** Dead-code view graph. */
  deadCodeGraph: GraphExport | undefined;
  isLoadingDeadCodeGraph: boolean;

  /** Hot-files view graph. */
  hotFilesGraph: GraphExport | undefined;
  isLoadingHotFilesGraph: boolean;

  /** Community list — used for legend labels and detail panel lookup. */
  communities?: CommunitySummaryItem[];

  /** Execution-flows data — used for the side panel and trace highlighting. */
  executionFlows?: ExecutionFlows;

  /**
   * Called when a view-mode change requires the consumer to (re)load
   * datasets that aren't already supplied. The shell does NOT fetch — the
   * wrapper observes this and triggers SWR / mutate as needed.
   */
  onViewModeChange?: (mode: ViewMode) => void;
  /** Same idea for drill-down. The shell exposes the current module path. */
  onModulePathChange?: (path: string[]) => void;

  /** Node-level interactions surfaced to the consumer. */
  onNodeClick?: (nodeId: string, nodeType: string) => void | Promise<void>;
  onNodeViewDocs?: (nodeId: string) => void;

  /**
   * Render the path-finder sub-panel. The shell only decides when to mount
   * it and provides path-finder context (initial from/to, callbacks).
   */
  renderPathFinder?: (props: {
    initialFrom: string;
    initialTo: string;
    onPathFound: (pathNodes: string[]) => void;
    onClear: () => void;
    onClose: () => void;
  }) => ReactNode;

  /**
   * Render the community detail panel. The shell only owns the
   * `communityId` selection state.
   */
  renderCommunityPanel?: (props: {
    communityId: number;
    onClose: () => void;
  }) => ReactNode;
}

// ---- Inner component (needs ReactFlowProvider) ----

function GraphFlowInner(props: GraphFlowProps) {
  const {
    moduleGraph,
    isLoadingModuleGraph,
    fullGraph,
    isLoadingFullGraph,
    architectureGraph,
    isLoadingArchitectureGraph,
    deadCodeGraph,
    isLoadingDeadCodeGraph,
    hotFilesGraph,
    isLoadingHotFilesGraph,
    communities,
    executionFlows,
    onViewModeChange,
    onModulePathChange,
    onNodeClick,
    onNodeViewDocs,
    renderPathFinder,
    renderCommunityPanel,
  } = props;

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
  const currentPrefix = modulePath.length > 0 ? (modulePath[modulePath.length - 1] ?? "") : "";
  const isDrilledDown = modulePath.length > 0;

  // Notify consumer of module-path changes (so it can fetch full graph etc.)
  useEffect(() => {
    onModulePathChange?.(modulePath);
  }, [modulePath, onModulePathChange]);

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
  const [searchResults, setSearchResults] = useState<string[]>([]);
  const [searchResultIndex, setSearchResultIndex] = useState(0);

  // Execution flow highlighting
  const [activeFlowIdx, setActiveFlowIdx] = useState<number | null>(null);
  const [showFlows, setShowFlows] = useState(false);

  // Community panel selection
  const [communityPanelId, setCommunityPanelId] = useState<number | null>(null);

  // Community filtering
  const [activeCommunities, setActiveCommunities] = useState<Set<number> | null>(null);

  // Layout mode & graph theme
  const [layoutMode, setLayoutMode] = useState<LayoutMode>("hierarchical");
  const [graphTheme, setGraphTheme] = useState<GraphTheme>("light");
  const isUnified = viewMode === "unified";
  const effectiveForce = layoutMode === "force" && !isUnified;

  // ---- Data selection (consumer supplies the data; shell picks the right slice) ----
  const isModuleView = viewMode === "module";

  const communityLabels = useMemo(() => {
    if (!communities) return undefined;
    const m = new Map<number, string>();
    for (const c of communities) m.set(c.community_id, c.label);
    return m;
  }, [communities]);

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
      case "unified":
        return fullGraph ? { nodes: fullGraph.nodes, links: fullGraph.links } : undefined;
      case "architecture":
        return architectureGraph
          ? { nodes: architectureGraph.nodes, links: architectureGraph.links }
          : undefined;
      case "dead":
        return deadCodeGraph
          ? { nodes: deadCodeGraph.nodes, links: deadCodeGraph.links }
          : undefined;
      case "hotfiles":
        return hotFilesGraph
          ? { nodes: hotFilesGraph.nodes, links: hotFilesGraph.links }
          : undefined;
      default:
        return undefined;
    }
  }, [viewMode, fullGraph, architectureGraph, deadCodeGraph, hotFilesGraph]);

  // ---- Layout selection ----
  const moduleLayoutInput = useMemo(() => {
    if (effectiveForce || !isModuleView) return { nodes: undefined, edges: undefined, fileEntries: undefined };
    if (!isDrilledDown) {
      return { nodes: moduleGraph?.nodes, edges: moduleGraph?.edges, fileEntries: undefined };
    }
    return {
      nodes: drillDownData?.moduleNodes,
      edges: drillDownData?.moduleEdges,
      fileEntries: drillDownData?.fileEntries,
    };
  }, [effectiveForce, isModuleView, isDrilledDown, moduleGraph, drillDownData]);

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
    !effectiveForce && !isModuleView ? fileGraphData?.nodes : undefined,
    !effectiveForce && !isModuleView ? fileGraphData?.links : undefined,
  );

  // Force layout
  const [forceNodes, setForceNodes] = useState<Node[]>([]);
  const [forceEdges, setForceEdges] = useState<Edge[]>([]);
  const [forceLayouting, setForceLayouting] = useState(false);
  const forceCacheKey = useRef("");

  const forceGraphData = useMemo(() => {
    if (!effectiveForce) return undefined;
    if (isModuleView) return fullGraph ? { nodes: fullGraph.nodes, links: fullGraph.links } : undefined;
    return fileGraphData;
  }, [effectiveForce, isModuleView, fullGraph, fileGraphData]);

  useEffect(() => {
    const nodes = forceGraphData?.nodes;
    const links = forceGraphData?.links;
    if (!nodes || !links || nodes.length === 0) {
      setForceNodes([]);
      setForceEdges([]);
      forceCacheKey.current = "";
      return;
    }
    const key = `force:${nodes.length}:${links.length}:${nodes[0]?.node_id}`;
    if (key === forceCacheKey.current) return;
    forceCacheKey.current = key;

    let cancelled = false;
    setForceLayouting(true);
    Promise.resolve().then(() => {
      if (cancelled) return;
      const result = computeForceLayout(nodes, links);
      setForceNodes(result.nodes);
      setForceEdges(result.edges);
      setForceLayouting(false);
    });
    return () => { cancelled = true; };
  }, [forceGraphData]);

  const currentNodes = effectiveForce ? forceNodes : (isModuleView ? moduleNodes : fileNodes);
  const currentEdges = effectiveForce ? forceEdges : (isModuleView ? moduleEdges : fileEdges);

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

  const { maxPagerank, medianPagerank } = useMemo(() => {
    const prs = currentNodes
      .filter((n) => n.type === "fileNode")
      .map((n) => ((n.data as Record<string, unknown>).pagerank as number) ?? 0);
    if (prs.length === 0) return { maxPagerank: 0, medianPagerank: 0 };
    const sorted = [...prs].sort((a, b) => a - b);
    return {
      maxPagerank: Math.max(...prs),
      medianPagerank: sorted[Math.floor(sorted.length / 2)] ?? 0,
    };
  }, [currentNodes]);

  // Data for inspection panel — map of node ID → FileNodeData, sorted pageranks/betweenness
  const { allNodeDataMap, sortedPageranks, sortedBetweenness } = useMemo(() => {
    const map = new Map<string, FileNodeData>();
    const prs: number[] = [];
    const bts: number[] = [];
    for (const n of currentNodes) {
      if (n.type === "fileNode") {
        const d = n.data as FileNodeData;
        map.set(n.id, d);
        prs.push(d.pagerank);
        bts.push(d.betweenness);
      }
    }
    prs.sort((a, b) => a - b);
    bts.sort((a, b) => a - b);
    return { allNodeDataMap: map, sortedPageranks: prs, sortedBetweenness: bts };
  }, [currentNodes]);

  const isLoading =
    (effectiveForce && isModuleView) ? isLoadingFullGraph :
    (isModuleView && !isDrilledDown) ? isLoadingModuleGraph :
    isDrilledDown ? isLoadingFullGraph :
    viewMode === "full" || viewMode === "unified" ? isLoadingFullGraph :
    viewMode === "architecture" ? isLoadingArchitectureGraph :
    viewMode === "dead" ? isLoadingDeadCodeGraph :
    viewMode === "hotfiles" ? isLoadingHotFilesGraph : false;
  const isLayouting = effectiveForce ? forceLayouting : (isModuleView ? moduleLayouting : fileLayouting);

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

  // Community dimming: compute set of node IDs whose community is filtered out
  const communityDimmedNodes = useMemo(() => {
    if (!activeCommunities) return null;
    const dimmed = new Set<string>();
    for (const n of filteredNodes) {
      if (n.type === "fileNode") {
        const cid = (n.data as { communityId?: number }).communityId;
        if (cid !== undefined && !activeCommunities.has(cid)) dimmed.add(n.id);
      }
    }
    return dimmed.size > 0 ? dimmed : null;
  }, [activeCommunities, filteredNodes]);

  // Hotspot / dead-code node sets (for unified view badges)
  const hotNodeIds = useMemo(() => {
    if (!hotFilesGraph) return new Set<string>();
    return new Set(hotFilesGraph.nodes.map((n) => n.node_id));
  }, [hotFilesGraph]);

  const deadNodeIds = useMemo(() => {
    if (!deadCodeGraph) return new Set<string>();
    return new Set(deadCodeGraph.nodes.map((n) => n.node_id));
  }, [deadCodeGraph]);

  // Tag nodes with isHotspot/isDead for unified view
  const displayNodes = useMemo(() => {
    if (!isUnified) return filteredNodes;
    return filteredNodes.map((n) => {
      if (n.type !== "fileNode") return n;
      const isHotspot = hotNodeIds.has(n.id);
      const isDead = deadNodeIds.has(n.id);
      if (!isHotspot && !isDead) return n;
      return { ...n, data: { ...(n.data as FileNodeData), isHotspot, isDead } };
    });
  }, [isUnified, filteredNodes, hotNodeIds, deadNodeIds]);

  // Fuse search index over visible nodes
  const fuseIndex = useMemo(() => {
    const items = filteredNodes.map((n) => ({ id: n.id, label: (n.data as { label?: string }).label ?? n.id }));
    return new Fuse(items, { keys: ["id", "label"], threshold: 0.4 });
  }, [filteredNodes]);

  // Search: compute dimmed set and result list when query is active
  useEffect(() => {
    if (!searchQuery || searchQuery.length < 2) {
      setSearchDimmedNodes(null);
      setSearchResults([]);
      setSearchResultIndex(0);
      return;
    }
    const results = fuseIndex.search(searchQuery);
    const matchIds = new Set(results.map((r) => r.item.id));
    const ids = results.map((r) => r.item.id);
    const dimmed = new Set<string>();
    for (const n of filteredNodes) {
      if (!matchIds.has(n.id)) dimmed.add(n.id);
    }
    setSearchDimmedNodes(dimmed);
    setSearchResults(ids);
    setSearchResultIndex(0);

    if (ids.length === 1) {
      setSelectedNodeId(ids[0]!);
    }

    if (ids.length > 0 && ids.length <= 20) {
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
    if (activeFlowIdx === null || !executionFlows) {
      if (activeFlowIdx === null && showFlows) {
        setHighlightedPath(new Set());
        setHighlightedEdges(new Set());
      }
      return;
    }
    const flow = executionFlows.flows[activeFlowIdx];
    if (!flow) return;
    const pathSet = new Set(flow.trace);
    const edgeKeys = new Set<string>();
    for (let i = 0; i < flow.trace.length - 1; i++) {
      edgeKeys.add(`${flow.trace[i]}→${flow.trace[i + 1]}`);
      edgeKeys.add(`${flow.trace[i + 1]}→${flow.trace[i]}`);
    }
    setHighlightedPath(pathSet);
    setHighlightedEdges(edgeKeys);

    if (viewMode === "module") {
      setViewMode("full");
      setModulePath([]);
      onViewModeChange?.("full");
    }

    setTimeout(() => {
      const traceNodes = flow.trace
        .map((id) => reactFlow.getNode(id))
        .filter(Boolean) as Node[];
      if (traceNodes.length > 0) {
        reactFlow.fitView({ nodes: traceNodes, padding: 0.3, duration: 600 });
      }
    }, 800);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeFlowIdx, executionFlows]);

  // Context value
  const ctxValue = useMemo<GraphContextValue>(
    () => ({
      highlightedPath,
      highlightedEdges,
      colorMode,
      viewMode,
      riskScores: new Map(),
      hoveredNodeId,
      connectedNodeIds,
      connectedEdgeIds,
      selectedNodeId,
      searchDimmedNodes,
      communityDimmedNodes,
      layoutMode,
      graphTheme,
      maxPagerank,
      medianPagerank,
    }),
    [highlightedPath, highlightedEdges, colorMode, viewMode, hoveredNodeId, connectedNodeIds, connectedEdgeIds, selectedNodeId, searchDimmedNodes, communityDimmedNodes, layoutMode, graphTheme, maxPagerank, medianPagerank],
  );

  // ---- Handlers ----

  const handleNodeClick: NodeMouseHandler = useCallback(
    (event, node) => {
      if (node.type === "moduleGroup" && isModuleView) {
        setModulePath((prev) => [...prev, node.id]);
        setSelectedNodeId(null);
        return;
      }
      if (colorMode === "community" && node.type === "fileNode") {
        const nodeData = node.data as FileNodeData;
        if (nodeData?.communityId !== undefined) {
          setCommunityPanelId(nodeData.communityId);
        }
      }

      if (selectedNodeId === node.id) {
        setSelectedNodeId(null);
        setSelectedNodeScreen(null);
      } else if (node.type === "fileNode") {
        setSelectedNodeId(node.id);
        setSelectedNodeScreen(null);
      } else {
        const mEvent = event as unknown as React.MouseEvent;
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
        onViewModeChange?.("full");
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
    [viewMode, reactFlow, onViewModeChange],
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
    onViewModeChange?.(v);
  }, [onViewModeChange]);

  const handleLayoutModeChange = useCallback((mode: LayoutMode) => {
    setLayoutMode(mode);
    if (mode === "force" && (viewMode === "module" || viewMode === "unified")) {
      handleViewChange("full");
    }
    hasFocusedRef.current = false;
  }, [viewMode, handleViewChange]);

  const handleGraphThemeChange = useCallback((theme: GraphTheme) => {
    setGraphTheme(theme);
  }, []);

  const panToNode = useCallback((nodeId: string) => {
    const rfNode = reactFlow.getNode(nodeId);
    if (rfNode) {
      reactFlow.fitView({ nodes: [rfNode], padding: 1.5, duration: 400, maxZoom: 1.5 });
    }
  }, [reactFlow]);

  const handleSearchKeyDown = useCallback((e: React.KeyboardEvent<HTMLInputElement>) => {
    if (searchResults.length === 0) {
      if (e.key === "Escape") { setSearchQuery(""); }
      return;
    }
    switch (e.key) {
      case "ArrowDown": {
        e.preventDefault();
        const next = (searchResultIndex + 1) % searchResults.length;
        setSearchResultIndex(next);
        panToNode(searchResults[next]!);
        break;
      }
      case "ArrowUp": {
        e.preventDefault();
        const prev = (searchResultIndex - 1 + searchResults.length) % searchResults.length;
        setSearchResultIndex(prev);
        panToNode(searchResults[prev]!);
        break;
      }
      case "Enter": {
        e.preventDefault();
        const id = searchResults[searchResultIndex];
        if (id) { setSelectedNodeId(id); panToNode(id); }
        break;
      }
      case "Escape":
        setSearchQuery("");
        break;
    }
  }, [searchResults, searchResultIndex, panToNode]);

  // Community filter handlers
  const allCommunityIds = useMemo(() => {
    const ids = new Set<number>();
    for (const n of filteredNodes) {
      if (n.type === "fileNode") {
        const cid = (n.data as { communityId?: number }).communityId;
        if (cid !== undefined) ids.add(cid);
      }
    }
    return ids;
  }, [filteredNodes]);

  const handleCommunityToggle = useCallback((cid: number) => {
    setActiveCommunities((prev) => {
      const current = prev ?? new Set(allCommunityIds);
      const next = new Set(current);
      if (next.has(cid)) next.delete(cid); else next.add(cid);
      if (next.size === allCommunityIds.size) return null;
      return next;
    });
  }, [allCommunityIds]);

  const handleToggleAllCommunities = useCallback((selectAll: boolean) => {
    setActiveCommunities(selectAll ? null : new Set<number>());
  }, []);

  const handleInspectNavigate = useCallback((nodeId: string) => {
    setSelectedNodeId(nodeId);
    setSelectedNodeScreen(null);
    panToNode(nodeId);
  }, [panToNode]);

  const handleInspectFindPath = useCallback(() => {
    if (selectedNodeId) {
      setPathFrom(selectedNodeId);
      setShowPathFinder(true);
    }
  }, [selectedNodeId]);

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
    if (isLayouting || filteredNodes.length === 0 || hasFocusedRef.current) return;
    hasFocusedRef.current = true;

    const timer = setTimeout(() => {
      switch (viewMode) {
        case "dead":
        case "hotfiles":
        case "architecture": {
          reactFlow.fitView({ padding: 0.3, duration: 600, maxZoom: 1.5 });
          return;
        }
        case "module": {
          reactFlow.fitView({ padding: 0.2, duration: 600, maxZoom: 1 });
          return;
        }
        default:
          break;
      }
      reactFlow.fitView({ padding: 0.15, duration: 600, maxZoom: 0.6 });
    }, 100);

    return () => clearTimeout(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
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
    <GraphProvider value={ctxValue}>
      <div className="relative w-full h-full" style={{ touchAction: "none", ...(graphTheme === "dark" ? { background: "#0f0f1a" } : {}) }} aria-label="Dependency graph">
        {isLayouting && (
          <div className="absolute inset-0 z-50 flex items-center justify-center bg-[var(--color-bg-root)]/60 backdrop-blur-sm rounded-lg">
            <div className="flex items-center gap-3 text-sm text-[var(--color-text-secondary)]">
              <Loader2 className="w-5 h-5 animate-spin text-[var(--color-accent-graph)]" />
              Computing layout...
            </div>
          </div>
        )}

        {displayNodes.length === 0 && !isLayouting ? (
          <div className="flex items-center justify-center h-full">
            <EmptyState
              title="No graph data"
              description="Check that the backend is running and this repo has been indexed."
            />
          </div>
        ) : (
        <ReactFlow
          nodes={displayNodes}
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
          nodesDraggable={effectiveForce}
          defaultEdgeOptions={{ type: "dependency" }}
        >
          <Background variant={BackgroundVariant.Dots} gap={16} size={0.5} color={graphTheme === "dark" ? "rgba(255,255,255,0.05)" : "rgba(255,255,255,0.03)"} />
          <Controls
            showInteractive={false}
            className={graphTheme === "dark"
              ? "!border-white/10 !bg-[#1a1a2e] !shadow-lg !shadow-black/40 [&>button]:!border-white/10 [&>button]:!bg-[#1a1a2e] [&>button]:!text-white/60 [&>button:hover]:!bg-[#252540] [&>button:hover]:!text-white"
              : "!border-[var(--color-border-default)] !bg-[var(--color-bg-elevated)] !shadow-lg !shadow-black/20 [&>button]:!border-[var(--color-border-default)] [&>button]:!bg-[var(--color-bg-elevated)] [&>button]:!text-[var(--color-text-secondary)] [&>button:hover]:!bg-[var(--color-bg-overlay)] [&>button:hover]:!text-[var(--color-text-primary)]"
            }
          />
          <MiniMap
            nodeColor={minimapNodeColor}
            maskColor={graphTheme === "dark" ? "rgba(0, 0, 0, 0.7)" : "rgba(0, 0, 0, 0.6)"}
            className={graphTheme === "dark"
              ? "!bg-[#1a1a2e] !border-white/10 !shadow-lg !shadow-black/40 !rounded-lg !hidden sm:!block"
              : "!bg-[var(--color-bg-surface)] !border-[var(--color-border-default)] !shadow-lg !shadow-black/20 !rounded-lg !hidden sm:!block"
            }
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
            searchMatchCount={searchResults.length}
            searchTotalCount={filteredNodes.length}
            onSearchKeyDown={handleSearchKeyDown}
            layoutMode={layoutMode}
            onLayoutModeChange={handleLayoutModeChange}
            graphTheme={graphTheme}
            onGraphThemeChange={handleGraphThemeChange}
          />
        </div>

        {/* Path Finder — rendered via consumer slot (data-coupled) */}
        {showPathFinder && renderPathFinder && (
          <div className="absolute top-14 right-3 z-10">
            {renderPathFinder({
              initialFrom: pathFrom,
              initialTo: pathTo,
              onPathFound: handlePathFound,
              onClear: handlePathClear,
              onClose: () => setShowPathFinder(false),
            })}
          </div>
        )}

        {/* Execution Flows Panel */}
        {showFlows && executionFlows && executionFlows.flows.length > 0 && (
          <div className="absolute top-14 right-3 z-10 w-[min(16rem,calc(100vw-1.5rem))]">
            <div className="rounded-lg border border-[var(--color-border-default)] bg-[var(--color-bg-elevated)]/95 backdrop-blur-sm shadow-lg shadow-black/20 p-3">
              <div className="flex items-center justify-between mb-2">
                <span className="text-[11px] font-medium text-[var(--color-text-primary)]">
                  Execution Flows
                </span>
                <span className="text-[10px] text-[var(--color-text-tertiary)]">
                  {executionFlows.flows.length} entry points
                </span>
              </div>
              <div className="space-y-1 max-h-60 overflow-y-auto">
                {executionFlows.flows.map((flow, idx) => (
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
            nodeCount={displayNodes.length}
            edgeCount={currentEdges.length}
            colorMode={colorMode}
            viewMode={viewMode}
            {...(communityLabels ? { communityLabels } : {})}
            onCommunityClick={setCommunityPanelId}
            activeCommunities={activeCommunities ?? undefined}
            onCommunityToggle={handleCommunityToggle}
            onToggleAllCommunities={handleToggleAllCommunities}
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

        {/* Community detail panel — rendered via consumer slot */}
        {communityPanelId !== null && renderCommunityPanel &&
          renderCommunityPanel({
            communityId: communityPanelId,
            onClose: () => setCommunityPanelId(null),
          })}

        {/* Node inspection panel — file nodes only, shown when no screen coords (keyboard nav or neighbor click) */}
        {selectedNodeId && !selectedNodeScreen && (() => {
          const nd = allNodeDataMap.get(selectedNodeId);
          if (!nd) return null;
          return (
            <GraphInspectionPanel
              nodeId={selectedNodeId}
              data={nd}
              edges={currentEdges}
              allNodes={allNodeDataMap}
              allPageranks={sortedPageranks}
              allBetweenness={sortedBetweenness}
              communityLabel={communityLabels?.get(nd.communityId)}
              onClose={() => { setSelectedNodeId(null); setSelectedNodeScreen(null); }}
              onNavigateToNode={handleInspectNavigate}
              onViewDocs={() => { onNodeViewDocs?.(selectedNodeId); }}
              onFindPath={handleInspectFindPath}
            />
          );
        })()}

        {/* Node detail tooltip */}
        {selectedNodeId && selectedNodeScreen && (() => {
          const rfNode = filteredNodes.find((n) => n.id === selectedNodeId);
          if (!rfNode) return null;
          const onExplore = rfNode.type === "moduleGroup" && isModuleView
            ? () => {
                setModulePath((prev) => [...prev, selectedNodeId]);
                setSelectedNodeId(null);
                setSelectedNodeScreen(null);
              }
            : undefined;
          return (
            <GraphTooltip
              nodeId={selectedNodeId}
              nodeType={rfNode.type ?? "fileNode"}
              data={rfNode.data as Record<string, unknown>}
              x={selectedNodeScreen.x}
              y={selectedNodeScreen.y}
              onClose={() => { setSelectedNodeId(null); setSelectedNodeScreen(null); }}
              onViewDocs={() => { onNodeViewDocs?.(selectedNodeId); setSelectedNodeId(null); setSelectedNodeScreen(null); }}
              {...(onExplore ? { onExplore } : {})}
            />
          );
        })()}
      </div>
    </GraphProvider>
  );
}

// ---- Public component ----

export function GraphFlow(props: GraphFlowProps) {
  return (
    <ReactFlowProvider>
      <GraphFlowInner {...props} />
    </ReactFlowProvider>
  );
}
