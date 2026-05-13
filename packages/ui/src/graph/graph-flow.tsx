"use client";

import {
  useCallback,
  useMemo,
  useState,
  useEffect,
  useRef,
  type ReactNode,
} from "react";
import { ChevronRight, Home } from "lucide-react";
import Fuse from "fuse.js";
import { Skeleton } from "../ui/skeleton";
import { EmptyState } from "../shared/empty-state";
import { GraphProvider, type GraphContextValue, type Signal } from "./context";
import { groupNodesAsModules, type FileNodeData, type ModuleNodeData } from "./elk-layout";

const EMPTY_STRING_SET = new Set<string>();
import { useExpandedModules } from "./use-expanded-modules";
import { GraphToolbar, type ColorMode, type ViewMode, type LayoutMode, type GraphTheme } from "./graph-toolbar";
import { GraphLegend } from "./graph-legend";
import { GraphContextMenu } from "./graph-context-menu";
import { GraphInspectionPanel } from "./graph-inspection-panel";
import type {
  GraphExport,
  ModuleGraph,
  ExecutionFlows,
  CommunitySummaryItem,
} from "@repowise-dev/types/graph";
import { SigmaCanvas, type SigmaCanvasHandle } from "./sigma/sigma-canvas";
import {
  fileGraphToGraphology,
  moduleGraphToGraphology,
  groupFilesAsModules,
} from "./sigma/graphology-adapter";
import { useEgoFilter } from "./sigma/use-ego-filter";
import {
  NODE_BASE_SIZES,
  EDGE_COLORS,
  getScaledNodeSize,
  getNodeMass,
  languageColor as sigmaLanguageColor,
} from "./sigma/constants";

export interface GraphFlowProps {
  moduleGraph: ModuleGraph | undefined;
  isLoadingModuleGraph: boolean;
  fullGraph: GraphExport | undefined;
  isLoadingFullGraph: boolean;
  architectureGraph: GraphExport | undefined;
  isLoadingArchitectureGraph: boolean;
  deadCodeGraph: GraphExport | undefined;
  isLoadingDeadCodeGraph: boolean;
  hotFilesGraph: GraphExport | undefined;
  isLoadingHotFilesGraph: boolean;
  communities?: CommunitySummaryItem[];
  executionFlows?: ExecutionFlows;
  initialViewMode?: ViewMode;
  initialSelectedNode?: string | null;
  onViewModeChange?: (mode: ViewMode) => void;
  onModulePathChange?: (path: string[]) => void;
  onExpandedModulesChange?: (expanded: Set<string>) => void;
  onNodeClick?: (nodeId: string, nodeType: string) => void | Promise<void>;
  onNodeViewDocs?: (nodeId: string) => void;
  renderPathFinder?: (props: {
    initialFrom: string;
    initialTo: string;
    onPathFound: (pathNodes: string[]) => void;
    onClear: () => void;
    onClose: () => void;
  }) => ReactNode;
  renderCommunityPanel?: (props: {
    communityId: number;
    onClose: () => void;
  }) => ReactNode;
  /** Fired when the community detail panel transitions to open. Lets the
   *  hosting page dismiss any competing right-rail panel (doc panel etc.)
   *  so the right side is a single sidebar. */
  onCommunityPanelOpen?: (communityId: number) => void;
}

export function GraphFlow(props: GraphFlowProps) {
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
    initialViewMode,
    initialSelectedNode,
    onViewModeChange,
    onModulePathChange,
    onExpandedModulesChange,
    onNodeClick,
    onNodeViewDocs,
    renderPathFinder,
    renderCommunityPanel,
    onCommunityPanelOpen,
  } = props;

  const sigmaRef = useRef<SigmaCanvasHandle>(null);
  const focusTimerRef = useRef<ReturnType<typeof setTimeout>>(undefined);

  // ---- Core state ----
  const [viewMode, setViewMode] = useState<ViewMode>(initialViewMode ?? "module");
  const [colorMode, setColorMode] = useState<ColorMode>(() => {
    if (typeof window !== "undefined") {
      const params = new URLSearchParams(window.location.search);
      const cm = params.get("colorMode");
      if (cm === "community" || cm === "language" || cm === "risk") return cm;
    }
    return "community";
  });
  const [highlightedPath, setHighlightedPath] = useState<Set<string>>(new Set());
  const [highlightedEdges, setHighlightedEdges] = useState<Set<string>>(new Set());
  const [showPathFinder, setShowPathFinder] = useState(false);
  const [layoutMode, setLayoutMode] = useState<LayoutMode>("force");
  const [graphTheme, setGraphTheme] = useState<GraphTheme>("light");

  const [egoDepth, setEgoDepth] = useState(0);

  const [visibleEdgeTypes, setVisibleEdgeTypes] = useState<Set<string>>(
    () => new Set(["import", "crossCommunity"]),
  );

  // Signal overlays (replaces separate view modes for dead/hot/arch)
  const [activeSignals, setActiveSignals] = useState<Set<Signal>>(() => {
    if (typeof window !== "undefined") {
      const params = new URLSearchParams(window.location.search);
      const vm = params.get("viewMode");
      if (vm === "dead") return new Set<Signal>(["dead"]);
      if (vm === "hotfiles") return new Set<Signal>(["hot"]);
      if (vm === "unified") return new Set<Signal>(["dead", "hot"]);
    }
    return new Set<Signal>();
  });
  const hideTests = activeSignals.has("hideTests");

  // Expand/collapse modules (replaces drill-down for most use cases)
  const { expandedModules, toggleModule, collapseAll } = useExpandedModules();
  const hasExpandedModules = expandedModules.size > 0;

  // Legacy drill-down (breadcrumb nav, kept for deep-dive via context menu)
  const [modulePath, setModulePath] = useState<string[]>([]);
  const currentPrefix = modulePath.length > 0 ? (modulePath[modulePath.length - 1] ?? "") : "";
  const isDrilledDown = modulePath.length > 0;

  useEffect(() => {
    onModulePathChange?.(modulePath);
  }, [modulePath, onModulePathChange]);

  useEffect(() => {
    onExpandedModulesChange?.(expandedModules);
  }, [expandedModules, onExpandedModulesChange]);

  // Context menu
  const [ctxMenu, setCtxMenu] = useState<{
    x: number; y: number; nodeId: string; nodeType: string;
  } | null>(null);

  // Path finder pre-fill
  const [pathFrom, setPathFrom] = useState("");
  const [pathTo, setPathTo] = useState("");

  // Hover & selection
  const [hoveredNodeId, setHoveredNodeId] = useState<string | null>(null);
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);

  // Search
  const [searchQuery, setSearchQuery] = useState("");
  const [searchDimmedNodes, setSearchDimmedNodes] = useState<Set<string> | null>(null);
  const [searchResults, setSearchResults] = useState<string[]>([]);
  const [searchResultIndex, setSearchResultIndex] = useState(0);

  // Execution flows
  const [activeFlowIdx, setActiveFlowIdx] = useState<number | null>(null);
  const [showFlows, setShowFlows] = useState(false);

  // Community panel & filtering
  const [communityPanelId, setCommunityPanelId] = useState<number | null>(null);
  const [activeCommunities, setActiveCommunities] = useState<Set<number> | null>(null);

  // Wrap the setter so legend-driven opens notify the host page; this lets
  // the page dismiss competing right-rail panels (doc panel) and keep the
  // right side a single coordinated surface.
  const openCommunityPanel = useCallback(
    (cid: number) => {
      setCommunityPanelId(cid);
      onCommunityPanelOpen?.(cid);
    },
    [onCommunityPanelOpen],
  );

  // ---- Derived state ----
  const isModuleView = viewMode === "module";
  const isUnified = viewMode === "unified";

  const communityLabels = useMemo(() => {
    if (!communities) return undefined;
    const m = new Map<number, string>();
    for (const c of communities) m.set(c.community_id, c.label);
    return m;
  }, [communities]);

  // Drill-down data
  const drillDownData = useMemo(() => {
    if (!isDrilledDown || !fullGraph) return null;
    return groupNodesAsModules(fullGraph.nodes, fullGraph.links, currentPrefix);
  }, [isDrilledDown, fullGraph, currentPrefix]);

  // File-level graph data for non-module views
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

  // Loading state
  const isLoading =
    (isModuleView && !isDrilledDown) ? isLoadingModuleGraph :
    isDrilledDown ? isLoadingFullGraph :
    viewMode === "full" || viewMode === "unified" ? isLoadingFullGraph :
    viewMode === "architecture" ? isLoadingArchitectureGraph :
    viewMode === "dead" ? isLoadingDeadCodeGraph :
    viewMode === "hotfiles" ? isLoadingHotFilesGraph : false;

  // Signal overlay node sets
  const hotNodeIds = useMemo(() => {
    if (!hotFilesGraph) return new Set<string>();
    return new Set(hotFilesGraph.nodes.map((n) => n.node_id));
  }, [hotFilesGraph]);

  const deadNodeIds = useMemo(() => {
    if (!deadCodeGraph) return new Set<string>();
    return new Set(deadCodeGraph.nodes.map((n) => n.node_id));
  }, [deadCodeGraph]);

  const hasDeadSignal = activeSignals.has("dead");
  const hasHotSignal = activeSignals.has("hot");

  // Pre-build indexes for O(1) module expansion lookups (Fix 1.1)
  const fullGraphIndexes = useMemo(() => {
    if (!fullGraph) return null;
    const moduleChildIndex = new Map<string, typeof fullGraph.nodes>();
    const nodeEdgeIndex = new Map<string, typeof fullGraph.links>();

    for (const node of fullGraph.nodes) {
      const parts = node.node_id.split("/");
      for (let depth = 1; depth < parts.length; depth++) {
        const prefix = parts.slice(0, depth).join("/");
        let children = moduleChildIndex.get(prefix);
        if (!children) { children = []; moduleChildIndex.set(prefix, children); }
        children.push(node);
      }
    }
    for (const link of fullGraph.links) {
      let srcEdges = nodeEdgeIndex.get(link.source);
      if (!srcEdges) { srcEdges = []; nodeEdgeIndex.set(link.source, srcEdges); }
      srcEdges.push(link);
      let tgtEdges = nodeEdgeIndex.get(link.target);
      if (!tgtEdges) { tgtEdges = []; nodeEdgeIndex.set(link.target, tgtEdges); }
      tgtEdges.push(link);
    }
    return { moduleChildIndex, nodeEdgeIndex };
  }, [fullGraph]);

  // Build Graphology graph for Sigma rendering
  const sigmaGraph = useMemo(() => {
    if (isModuleView) {
      if (isDrilledDown && fullGraph) {
        return groupFilesAsModules(fullGraph, { prefix: currentPrefix });
      }

      if (!moduleGraph) return null;

      if (expandedModules.size === 0 || !fullGraph || !fullGraphIndexes) {
        return moduleGraphToGraphology(moduleGraph, communities ? { communities } : {});
      }

      const graph = moduleGraphToGraphology(moduleGraph, communities ? { communities } : {});

      for (const moduleId of expandedModules) {
        if (!graph.hasNode(moduleId)) continue;

        const modAttrs = graph.getNodeAttributes(moduleId);
        const modX = modAttrs.x;
        const modY = modAttrs.y;

        graph.dropNode(moduleId);

        const childNodes = fullGraphIndexes.moduleChildIndex.get(moduleId) ?? [];

        const nodeCount = fullGraph.nodes.length;
        const jitter = 30;

        for (const node of childNodes) {
          if (graph.hasNode(node.node_id)) continue;
          const baseSize = node.is_entry_point
            ? NODE_BASE_SIZES.entryPoint
            : node.is_test ? NODE_BASE_SIZES.test : NODE_BASE_SIZES.file;
          let size = getScaledNodeSize(baseSize, nodeCount);
          size *= Math.min(1 + node.pagerank * 2, 2);
          const color = sigmaLanguageColor(node.language);

          graph.addNode(node.node_id, {
            x: modX + (Math.random() - 0.5) * jitter,
            y: modY + (Math.random() - 0.5) * jitter,
            size,
            color,
            label: node.node_id.split("/").pop() ?? node.node_id,
            nodeType: "file",
            fullPath: node.node_id,
            language: node.language,
            communityId: node.community_id,
            pagerank: node.pagerank,
            betweenness: node.betweenness,
            isTest: node.is_test,
            isEntryPoint: node.is_entry_point,
            hasDoc: node.has_doc,
            symbolCount: node.symbol_count,
            mass: getNodeMass("file", nodeCount),
            originalColor: color,
          });
        }

        const childIds = new Set(childNodes.map((n) => n.node_id));
        const seenEdges = new Set<string>();
        for (const childId of childIds) {
          const edges = fullGraphIndexes.nodeEdgeIndex.get(childId) ?? [];
          for (const link of edges) {
            const edgeKey = link.source + "→" + link.target;
            if (seenEdges.has(edgeKey)) continue;
            seenEdges.add(edgeKey);

            const srcInModule = childIds.has(link.source);
            const tgtInModule = childIds.has(link.target);

          if (srcInModule && tgtInModule) {
            if (!graph.hasEdge(edgeKey) && graph.hasNode(link.source) && graph.hasNode(link.target)) {
              graph.addEdgeWithKey(edgeKey, link.source, link.target, {
                size: 0.4,
                color: EDGE_COLORS.internal,
                type: "curved",
                curvature: 0.15,
                edgeKind: "internal",
                importedNames: link.imported_names,
                edgeCount: 1,
              });
            }
          } else if (srcInModule && graph.hasNode(link.target)) {
            if (!graph.hasEdge(edgeKey)) {
              graph.addEdgeWithKey(edgeKey, link.source, link.target, {
                size: 0.5,
                color: EDGE_COLORS.import,
                type: "curved",
                curvature: 0.15,
                edgeKind: "import",
                importedNames: link.imported_names,
                edgeCount: 1,
              });
            }
          } else if (tgtInModule && graph.hasNode(link.source)) {
            if (!graph.hasEdge(edgeKey)) {
              graph.addEdgeWithKey(edgeKey, link.source, link.target, {
                size: 0.5,
                color: EDGE_COLORS.import,
                type: "curved",
                curvature: 0.15,
                edgeKind: "import",
                importedNames: link.imported_names,
                edgeCount: 1,
              });
            }
          }
          }
        }
      }

      return graph;
    }

    const graphData = fileGraphData;
    if (!graphData) return null;

    const signals: { hotNodeIds?: Set<string>; deadNodeIds?: Set<string> } = {};
    if (hasHotSignal || isUnified) signals.hotNodeIds = hotNodeIds;
    if (hasDeadSignal || isUnified) signals.deadNodeIds = deadNodeIds;

    return fileGraphToGraphology(
      { nodes: graphData.nodes, links: graphData.links },
      { signals },
    );
  }, [isModuleView, isDrilledDown, fullGraph, currentPrefix, moduleGraph, communities, expandedModules, fileGraphData, hasHotSignal, hasDeadSignal, isUnified, hotNodeIds, deadNodeIds, fullGraphIndexes]);

  const { hiddenNodes, isActive: isEgoActive, visibleCount: egoVisibleCount } = useEgoFilter({
    graph: sigmaGraph,
    selectedNodeId,
    depth: egoDepth,
  });

  // Node data maps (sorted metrics moved into GraphInspectionPanel)
  const sigmaNodeMaps = useMemo(() => {
    if (!sigmaGraph) return null;

    const fileMap = new Map<string, FileNodeData>();
    const modMap = new Map<string, ModuleNodeData>();

    sigmaGraph.forEachNode((nodeId, attrs) => {
      if (attrs.nodeType === "file") {
        const fileData: FileNodeData = {
          nodeType: "file",
          label: attrs.label,
          fullPath: attrs.fullPath,
          language: attrs.language,
          symbolCount: attrs.symbolCount,
          pagerank: attrs.pagerank,
          betweenness: attrs.betweenness,
          communityId: attrs.communityId,
          isTest: attrs.isTest,
          isEntryPoint: attrs.isEntryPoint,
          hasDoc: attrs.hasDoc,
        };
        if (attrs.isHotspot) fileData.isHotspot = true;
        if (attrs.isDead) fileData.isDead = true;
        fileMap.set(nodeId, fileData);
      } else if (attrs.nodeType === "module") {
        modMap.set(nodeId, {
          nodeType: "module",
          label: attrs.label,
          fullPath: attrs.fullPath,
          fileCount: attrs.fileCount ?? 0,
          symbolCount: attrs.symbolCount,
          avgPagerank: attrs.avgPagerank ?? 0,
          docCoveragePct: attrs.docCoveragePct ?? 0,
          dominantCommunityId: attrs.dominantCommunityId,
        });
      }
    });

    return { fileMap, modMap };
  }, [sigmaGraph]);

  const effectiveNodeDataMap = sigmaNodeMaps?.fileMap ?? new Map<string, FileNodeData>();
  const effectiveModuleDataMap = sigmaNodeMaps?.modMap ?? new Map<string, ModuleNodeData>();

  // Community dimming
  const communityDimmedNodes = useMemo(() => {
    if (!activeCommunities) return null;
    const dimmed = new Set<string>();
    if (sigmaGraph) {
      sigmaGraph.forEachNode((nodeId, attrs) => {
        if (attrs.nodeType === "file" && !activeCommunities.has(attrs.communityId)) {
          dimmed.add(nodeId);
        }
      });
    }
    return dimmed.size > 0 ? dimmed : null;
  }, [activeCommunities, sigmaGraph]);

  // Search
  const fuseIndex = useMemo(() => {
    if (!sigmaGraph) return new Fuse<{ id: string; label: string }>([], { keys: ["id", "label"], threshold: 0.4 });
    const items: { id: string; label: string }[] = [];
    sigmaGraph.forEachNode((id, attrs) => {
      if (hideTests && attrs.isTest) return;
      items.push({ id, label: attrs.label });
    });
    return new Fuse(items, { keys: ["id", "label"], threshold: 0.4 });
  }, [sigmaGraph, hideTests]);

  const searchTimerRef = useRef<ReturnType<typeof setTimeout>>(undefined);
  useEffect(() => {
    if (!searchQuery || searchQuery.length < 2) {
      clearTimeout(searchTimerRef.current);
      setSearchDimmedNodes(null);
      setSearchResults([]);
      setSearchResultIndex(0);
      return;
    }
    clearTimeout(searchTimerRef.current);
    searchTimerRef.current = setTimeout(() => {
      const results = fuseIndex.search(searchQuery);
      const matchIds = new Set(results.map((r) => r.item.id));
      const ids = results.map((r) => r.item.id);
      const dimmed = new Set<string>();

      if (sigmaGraph) {
        sigmaGraph.forEachNode((nodeId) => {
          if (!matchIds.has(nodeId)) dimmed.add(nodeId);
        });
      }

      setSearchDimmedNodes(dimmed);
      setSearchResults(ids);
      setSearchResultIndex(0);

      if (ids.length === 1) {
        setSelectedNodeId(ids[0]!);
      }

      if (ids.length > 0 && ids.length <= 20) {
        sigmaRef.current?.focusNode(ids[0]!);
      }
    }, 150);
    return () => clearTimeout(searchTimerRef.current);
  }, [searchQuery, fuseIndex, sigmaGraph]);

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

    clearTimeout(focusTimerRef.current);
    focusTimerRef.current = setTimeout(() => {
      const firstNode = flow.trace[0];
      if (firstNode) sigmaRef.current?.focusNode(firstNode);
    }, 800);
    return () => clearTimeout(focusTimerRef.current);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeFlowIdx, executionFlows]);

  // Context value (hover fields use static empty sets — highlighting handled by Sigma reducers)
  const ctxValue = useMemo<GraphContextValue>(
    () => ({
      highlightedPath,
      highlightedEdges,
      colorMode,
      viewMode,
      hoveredNodeId,
      connectedNodeIds: EMPTY_STRING_SET,
      connectedEdgeIds: EMPTY_STRING_SET,
      selectedNodeId,
      searchDimmedNodes,
      communityDimmedNodes,
      layoutMode,
      graphTheme,
      maxPagerank: 0,
      medianPagerank: 0,
      expandedModules,
      activeSignals,
      egoDepth,
      visibleEdgeTypes,
    }),
    [highlightedPath, highlightedEdges, colorMode, viewMode, hoveredNodeId, selectedNodeId, searchDimmedNodes, communityDimmedNodes, layoutMode, graphTheme, expandedModules, activeSignals, egoDepth, visibleEdgeTypes],
  );

  // ---- Handlers ----

  const handleSigmaDoubleClick = useCallback(
    (nodeId: string, nodeType: string) => {
      if (nodeType === "module") {
        toggleModule(nodeId);
      } else {
        onNodeViewDocs?.(nodeId);
      }
    },
    [onNodeViewDocs, toggleModule],
  );

  const handleSigmaNodeClick = useCallback(
    (nodeId: string, _nodeType: string) => {
      if (selectedNodeId === nodeId) {
        setSelectedNodeId(null);
        setEgoDepth(0);
      } else {
        setSelectedNodeId(nodeId);
      }
    },
    [selectedNodeId],
  );

  const handleSigmaNodeContextMenu = useCallback(
    (event: MouseEvent, nodeId: string, nodeType: string) => {
      setCtxMenu({
        x: event.clientX,
        y: event.clientY,
        nodeId,
        nodeType: nodeType === "module" ? "moduleGroup" : "fileNode",
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

  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      const target = e.target as HTMLElement;
      if (target.tagName === "INPUT" || target.tagName === "TEXTAREA" || target.isContentEditable) return;

      switch (e.key) {
        case "f":
          e.preventDefault();
          sigmaRef.current?.fitView();
          break;
        case "Escape":
          setSelectedNodeId(null);
          setEgoDepth(0);
          setSearchQuery("");
          setCtxMenu(null);
          setCommunityPanelId(null);
          break;
        case "1":
          setColorMode("language");
          break;
        case "2":
          setColorMode("community");
          break;
        case "3":
          setColorMode("risk");
          break;
        case "/":
          e.preventDefault();
          document.querySelector<HTMLInputElement>('[aria-label="Search graph nodes"]')?.focus();
          break;
      }

      if ((e.metaKey || e.ctrlKey) && e.key === "k") {
        e.preventDefault();
        document.querySelector<HTMLInputElement>('[aria-label="Search graph nodes"]')?.focus();
      }
    };

    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, []);

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
      clearTimeout(focusTimerRef.current);
      focusTimerRef.current = setTimeout(() => {
        if (pathNodes.length > 0) {
          sigmaRef.current?.focusNode(pathNodes[0]!);
        }
      }, 800);
    },
    [viewMode, onViewModeChange],
  );

  const handlePathClear = useCallback(() => {
    setHighlightedPath(new Set());
    setHighlightedEdges(new Set());
  }, []);

  const handleFitView = useCallback(() => {
    sigmaRef.current?.fitView();
  }, []);

  const handleViewChange = useCallback((v: ViewMode) => {
    setViewMode(v);
    setLayoutMode("force");
    setModulePath([]);
    setHighlightedPath(new Set());
    setHighlightedEdges(new Set());
    setSelectedNodeId(null);
    onViewModeChange?.(v);
  }, [onViewModeChange]);

  const handleLayoutModeChange = useCallback((mode: LayoutMode) => {
    setLayoutMode(mode);
  }, []);

  const handleGraphThemeChange = useCallback((theme: GraphTheme) => {
    setGraphTheme(theme);
  }, []);

  const handleSignalToggle = useCallback((signal: Signal) => {
    setActiveSignals((prev) => {
      const next = new Set(prev);
      if (next.has(signal)) next.delete(signal);
      else next.add(signal);
      return next;
    });
  }, []);

  const handleEdgeTypeToggle = useCallback((edgeType: string) => {
    setVisibleEdgeTypes((prev) => {
      const next = new Set(prev);
      if (next.has(edgeType)) {
        if (next.size > 1) next.delete(edgeType);
      } else {
        next.add(edgeType);
      }
      return next;
    });
  }, []);

  const panToNode = useCallback((nodeId: string) => {
    sigmaRef.current?.focusNode(nodeId);
  }, []);

  const initialNodeApplied = useRef(false);
  useEffect(() => {
    if (initialNodeApplied.current || !initialSelectedNode || !sigmaGraph) return;
    if (sigmaGraph.hasNode(initialSelectedNode)) {
      initialNodeApplied.current = true;
      setSelectedNodeId(initialSelectedNode);
      setTimeout(() => panToNode(initialSelectedNode), 300);
    }
  }, [initialSelectedNode, sigmaGraph, panToNode]);

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
    if (sigmaGraph) {
      sigmaGraph.forEachNode((_nodeId, attrs) => {
        if (attrs.nodeType === "file") ids.add(attrs.communityId);
      });
    }
    return ids;
  }, [sigmaGraph]);

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
    panToNode(nodeId);
  }, [panToNode]);

  const handleInspectFindPath = useCallback(() => {
    if (selectedNodeId) {
      setPathFrom(selectedNodeId);
      setShowPathFinder(true);
    }
  }, [selectedNodeId]);

  const handleInspectExpandModule = useCallback(() => {
    if (selectedNodeId) {
      toggleModule(selectedNodeId);
    }
  }, [selectedNodeId, toggleModule]);

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

  if (isLoading) return <Skeleton className="h-full w-full rounded-lg" />;

  return (
    <GraphProvider value={ctxValue}>
      <div className="relative w-full h-full" style={{ touchAction: "none", ...(graphTheme === "dark" ? { background: "#0f0f1a" } : {}) }} aria-label="Dependency graph">
        {sigmaGraph ? (
          <SigmaCanvas
            ref={sigmaRef}
            graph={sigmaGraph}
            layoutMode={layoutMode}
            viewMode={viewMode}
            selectedNodeId={selectedNodeId}
            hoveredNodeId={hoveredNodeId}
            highlightedPath={highlightedPath}
            highlightedEdges={highlightedEdges}
            searchDimmedNodes={searchDimmedNodes}
            communityDimmedNodes={communityDimmedNodes}
            colorMode={colorMode}
            activeSignals={activeSignals}
            graphTheme={graphTheme}
            fileNodes={fileGraphData?.nodes}
            fileEdges={fileGraphData?.links}
            moduleNodes={isModuleView ? moduleGraph?.nodes : undefined}
            moduleEdges={isModuleView ? moduleGraph?.edges : undefined}
            onNodeClick={handleSigmaNodeClick}
            onNodeDoubleClick={handleSigmaDoubleClick}
            onNodeHover={setHoveredNodeId}
            onNodeContextMenu={handleSigmaNodeContextMenu}
            onStageClick={() => setSelectedNodeId(null)}
            hiddenNodes={isEgoActive ? hiddenNodes : undefined}
            visibleEdgeTypes={visibleEdgeTypes}
          />
        ) : !isLoading ? (
          <div className="flex items-center justify-center h-full">
            <EmptyState
              title="No graph data"
              description="Check that the backend is running and this repo has been indexed."
            />
          </div>
        ) : null}

        {/* Ego indicator or breadcrumb */}
        {isEgoActive && selectedNodeId ? (
          <div className="absolute top-3 left-3 z-10">
            <div className="flex items-center gap-2 rounded-lg border border-[var(--color-accent-graph)]/30 bg-[var(--color-bg-elevated)]/90 backdrop-blur-sm px-2.5 py-1.5 shadow-lg shadow-black/20">
              <span className="text-[10px] text-[var(--color-accent-graph)]">
                Showing {egoVisibleCount} nodes within {egoDepth} hop{egoDepth === 1 ? "" : "s"} of{" "}
                <span className="font-mono font-medium">{selectedNodeId.split("/").pop()}</span>
              </span>
              <button
                onClick={() => setEgoDepth(0)}
                className="text-[var(--color-text-tertiary)] hover:text-[var(--color-text-primary)] text-[10px]"
              >
                Clear
              </button>
            </div>
          </div>
        ) : isModuleView && isDrilledDown ? (
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
        ) : null}

        {/* Toolbar */}
        <div className="absolute top-3 right-3 z-10">
          <GraphToolbar
            viewMode={viewMode}
            onViewChange={handleViewChange}
            colorMode={colorMode}
            onColorModeChange={setColorMode}
            hideTests={hideTests}
            onHideTestsChange={(v) => {
              setActiveSignals(prev => {
                const next = new Set(prev);
                v ? next.add("hideTests") : next.delete("hideTests");
                return next;
              });
            }}
            onFitView={handleFitView}
            showPathFinder={showPathFinder}
            onTogglePathFinder={() => setShowPathFinder((s) => !s)}
            showFlows={showFlows}
            onToggleFlows={() => { setShowFlows((s) => !s); setActiveFlowIdx(null); }}
            searchQuery={searchQuery}
            onSearchChange={setSearchQuery}
            searchMatchCount={searchResults.length}
            searchTotalCount={sigmaGraph?.order ?? 0}
            onSearchKeyDown={handleSearchKeyDown}
            layoutMode={layoutMode}
            onLayoutModeChange={handleLayoutModeChange}
            graphTheme={graphTheme}
            onGraphThemeChange={handleGraphThemeChange}
          />
        </div>

        {/* Path Finder */}
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
            nodeCount={sigmaGraph?.order ?? 0}
            edgeCount={sigmaGraph?.size ?? 0}
            colorMode={colorMode}
            viewMode={viewMode}
            {...(communityLabels ? { communityLabels } : {})}
            onCommunityClick={openCommunityPanel}
            activeCommunities={activeCommunities ?? undefined}
            onCommunityToggle={handleCommunityToggle}
            onToggleAllCommunities={handleToggleAllCommunities}
            visibleEdgeTypes={visibleEdgeTypes}
            onEdgeTypeToggle={handleEdgeTypeToggle}
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
        {communityPanelId !== null && renderCommunityPanel &&
          renderCommunityPanel({
            communityId: communityPanelId,
            onClose: () => setCommunityPanelId(null),
          })}

        {/* Inspection panel — works for both file and module nodes */}
        {selectedNodeId && (() => {
          const fileNd = effectiveNodeDataMap.get(selectedNodeId);
          const modNd = effectiveModuleDataMap.get(selectedNodeId);
          const nd = fileNd ?? modNd;
          if (!nd) return null;
          return (
            <GraphInspectionPanel
              nodeId={selectedNodeId}
              data={nd}
              graph={sigmaGraph}
              allNodes={effectiveNodeDataMap}
              communityLabel={fileNd ? communityLabels?.get(fileNd.communityId) : undefined}
              onClose={() => { setSelectedNodeId(null); }}
              onNavigateToNode={handleInspectNavigate}
              onViewDocs={() => { onNodeViewDocs?.(selectedNodeId); }}
              onFindPath={handleInspectFindPath}
              onExpandModule={modNd ? handleInspectExpandModule : undefined}
              egoDepth={egoDepth}
              onEgoDepthChange={setEgoDepth}
              egoVisibleCount={egoVisibleCount}
            />
          );
        })()}
      </div>
    </GraphProvider>
  );
}

