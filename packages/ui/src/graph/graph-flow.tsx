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
import { GraphProvider, type GraphContextValue, type Signal } from "./context";
import { groupNodesAsModules, type FileNodeData, type ModuleNodeData } from "./elk-layout";
import { useForceLayout, useModuleForceLayout } from "./use-force-layout";
import { useModuleElkLayout, useFileElkLayout } from "./use-elk-layout";
import { useExpandedModules } from "./use-expanded-modules";
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
import { SigmaCanvas, type SigmaCanvasHandle } from "./sigma/sigma-canvas";
import {
  fileGraphToGraphology,
  moduleGraphToGraphology,
  groupFilesAsModules,
} from "./sigma/graphology-adapter";
import {
  NODE_BASE_SIZES,
  EDGE_COLORS,
  getScaledNodeSize,
  getNodeMass,
  languageColor as sigmaLanguageColor,
} from "./sigma/constants";

const nodeTypes = {
  moduleGroup: ModuleGroupNode,
  fileNode: FileNode,
};

const edgeTypes = {
  dependency: DependencyEdge,
};

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
}

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
    initialViewMode,
    initialSelectedNode,
    onViewModeChange,
    onModulePathChange,
    onExpandedModulesChange,
    onNodeClick,
    onNodeViewDocs,
    renderPathFinder,
    renderCommunityPanel,
  } = props;

  const reactFlow = useReactFlow();
  const sigmaRef = useRef<SigmaCanvasHandle>(null);

  // ---- Core state ----
  const [viewMode, setViewMode] = useState<ViewMode>(initialViewMode ?? "module");
  const [colorMode, setColorMode] = useState<ColorMode>(() => {
    if (typeof window !== "undefined") {
      const params = new URLSearchParams(window.location.search);
      const cm = params.get("colorMode");
      if (cm === "community" || cm === "language" || cm === "risk") return cm;
    }
    return "language";
  });
  const [highlightedPath, setHighlightedPath] = useState<Set<string>>(new Set());
  const [highlightedEdges, setHighlightedEdges] = useState<Set<string>>(new Set());
  const [showPathFinder, setShowPathFinder] = useState(false);
  const [layoutMode, setLayoutMode] = useState<LayoutMode>("force");
  const [graphTheme, setGraphTheme] = useState<GraphTheme>("light");

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

  // ---- Derived state ----
  const isModuleView = viewMode === "module";
  const isUnified = viewMode === "unified";
  const isSigmaMode = layoutMode === "sigma";
  const effectiveForce = layoutMode === "force" && !isUnified;

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

  // ---- Layout: Module force (Phase 3A) ----
  const moduleForceInput = useMemo(() => {
    if (!effectiveForce || !isModuleView || isDrilledDown) return { nodes: undefined, edges: undefined };
    return { nodes: moduleGraph?.nodes, edges: moduleGraph?.edges };
  }, [effectiveForce, isModuleView, isDrilledDown, moduleGraph]);

  const {
    nodes: moduleForceNodes,
    edges: moduleForceEdges,
    isLayouting: moduleForceLayouting,
  } = useModuleForceLayout(moduleForceInput.nodes, moduleForceInput.edges);

  // ---- Layout: Module ELK (fallback for hierarchical mode) ----
  const moduleElkInput = useMemo(() => {
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
    nodes: moduleElkNodes,
    edges: moduleElkEdges,
    isLayouting: moduleElkLayouting,
  } = useModuleElkLayout(moduleElkInput.nodes, moduleElkInput.edges, moduleElkInput.fileEntries);

  // ---- Layout: File-level force (for non-module force views) ----
  const fileForceInput = useMemo(() => {
    if (!effectiveForce || isModuleView) return { nodes: undefined, edges: undefined };
    return { nodes: fileGraphData?.nodes, edges: fileGraphData?.links };
  }, [effectiveForce, isModuleView, fileGraphData]);

  const {
    nodes: fileForceNodes,
    edges: fileForceEdges,
    isLayouting: fileForceLayouting,
  } = useForceLayout(fileForceInput.nodes, fileForceInput.edges);

  // ---- Layout: File-level ELK (for non-module hierarchical views) ----
  const {
    nodes: fileElkNodes,
    edges: fileElkEdges,
    isLayouting: fileElkLayouting,
  } = useFileElkLayout(
    !effectiveForce && !isModuleView ? fileGraphData?.nodes : undefined,
    !effectiveForce && !isModuleView ? fileGraphData?.links : undefined,
  );

  // ---- Select active layout output ----
  const currentNodes = useMemo(() => {
    if (effectiveForce) {
      return isModuleView ? moduleForceNodes : fileForceNodes;
    }
    return isModuleView ? moduleElkNodes : fileElkNodes;
  }, [effectiveForce, isModuleView, moduleForceNodes, fileForceNodes, moduleElkNodes, fileElkNodes]);

  const currentEdges = useMemo(() => {
    if (effectiveForce) {
      return isModuleView ? moduleForceEdges : fileForceEdges;
    }
    return isModuleView ? moduleElkEdges : fileElkEdges;
  }, [effectiveForce, isModuleView, moduleForceEdges, fileForceEdges, moduleElkEdges, fileElkEdges]);

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

  // Node data map for inspection panel (supports both file and module nodes)
  const { allNodeDataMap, allModuleDataMap, sortedPageranks, sortedBetweenness } = useMemo(() => {
    const fileMap = new Map<string, FileNodeData>();
    const modMap = new Map<string, ModuleNodeData>();
    const prs: number[] = [];
    const bts: number[] = [];
    for (const n of currentNodes) {
      if (n.type === "fileNode") {
        const d = n.data as FileNodeData;
        fileMap.set(n.id, d);
        prs.push(d.pagerank);
        bts.push(d.betweenness);
      } else if (n.type === "moduleGroup") {
        modMap.set(n.id, n.data as ModuleNodeData);
      }
    }
    prs.sort((a, b) => a - b);
    bts.sort((a, b) => a - b);
    return { allNodeDataMap: fileMap, allModuleDataMap: modMap, sortedPageranks: prs, sortedBetweenness: bts };
  }, [currentNodes]);

  // Loading / layouting states
  const isLoading =
    (effectiveForce && isModuleView) ? isLoadingModuleGraph :
    (isModuleView && !isDrilledDown) ? isLoadingModuleGraph :
    isDrilledDown ? isLoadingFullGraph :
    viewMode === "full" || viewMode === "unified" ? isLoadingFullGraph :
    viewMode === "architecture" ? isLoadingArchitectureGraph :
    viewMode === "dead" ? isLoadingDeadCodeGraph :
    viewMode === "hotfiles" ? isLoadingHotFilesGraph : false;

  const isLayouting = isSigmaMode
    ? false
    : effectiveForce
      ? (isModuleView ? moduleForceLayouting : fileForceLayouting)
      : (isModuleView ? moduleElkLayouting : fileElkLayouting);

  // Hover highlighting
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

  // Tag nodes with signal badges
  const displayNodes = useMemo(() => {
    const needsSignalBadges = hasDeadSignal || hasHotSignal || isUnified;
    if (!needsSignalBadges) return filteredNodes;
    return filteredNodes.map((n) => {
      if (n.type === "fileNode") {
        const isHotspot = (hasHotSignal || isUnified) && hotNodeIds.has(n.id);
        const isDead = (hasDeadSignal || isUnified) && deadNodeIds.has(n.id);
        if (!isHotspot && !isDead) return n;
        return { ...n, data: { ...(n.data as FileNodeData), isHotspot, isDead } };
      }
      if (n.type === "moduleGroup" && (hasDeadSignal || hasHotSignal)) {
        const modData = n.data as ModuleNodeData;
        const prefix = modData.fullPath + "/";
        let deadCount = 0;
        let hotCount = 0;
        if (hasDeadSignal) for (const id of deadNodeIds) { if (id.startsWith(prefix) || id === modData.fullPath) deadCount++; }
        if (hasHotSignal) for (const id of hotNodeIds) { if (id.startsWith(prefix) || id === modData.fullPath) hotCount++; }
        if (deadCount === 0 && hotCount === 0) return n;
        return { ...n, data: { ...modData, deadCount, hotCount } };
      }
      return n;
    });
  }, [filteredNodes, hasDeadSignal, hasHotSignal, isUnified, hotNodeIds, deadNodeIds]);

  // Build Graphology graph for Sigma mode
  const sigmaGraph = useMemo(() => {
    if (!isSigmaMode) return null;

    if (isModuleView) {
      if (isDrilledDown && fullGraph) {
        return groupFilesAsModules(fullGraph, { prefix: currentPrefix });
      }

      if (!moduleGraph) return null;

      if (expandedModules.size === 0 || !fullGraph) {
        return moduleGraphToGraphology(moduleGraph, communities ? { communities } : {});
      }

      const graph = moduleGraphToGraphology(moduleGraph, communities ? { communities } : {});

      for (const moduleId of expandedModules) {
        if (!graph.hasNode(moduleId)) continue;

        const modAttrs = graph.getNodeAttributes(moduleId);
        const modX = modAttrs.x;
        const modY = modAttrs.y;

        graph.dropNode(moduleId);

        const prefix = moduleId + "/";
        const childNodes = fullGraph.nodes.filter(
          (n) => n.node_id.startsWith(prefix) || n.node_id === moduleId,
        );

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
        for (const link of fullGraph.links) {
          const srcInModule = childIds.has(link.source);
          const tgtInModule = childIds.has(link.target);
          const edgeKey = link.source + "→" + link.target;

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
  }, [isSigmaMode, isModuleView, isDrilledDown, fullGraph, currentPrefix, moduleGraph, communities, expandedModules, fileGraphData, hasHotSignal, hasDeadSignal, isUnified, hotNodeIds, deadNodeIds]);

  // Sigma-mode data maps (equivalent to allNodeDataMap etc for React Flow mode)
  const sigmaDataMaps = useMemo(() => {
    if (!isSigmaMode || !sigmaGraph) return null;

    const fileMap = new Map<string, FileNodeData>();
    const modMap = new Map<string, ModuleNodeData>();
    const prs: number[] = [];
    const bts: number[] = [];

    sigmaGraph.forEachNode((nodeId, attrs) => {
      if (attrs.nodeType === "file") {
        const fileData: FileNodeData = {
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
        if (attrs.isHotspot) (fileData as Record<string, unknown>).isHotspot = true;
        if (attrs.isDead) (fileData as Record<string, unknown>).isDead = true;
        fileMap.set(nodeId, fileData);
        prs.push(attrs.pagerank);
        bts.push(attrs.betweenness);
      } else if (attrs.nodeType === "module") {
        modMap.set(nodeId, {
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

    prs.sort((a, b) => a - b);
    bts.sort((a, b) => a - b);

    return { fileMap, modMap, sortedPageranks: prs, sortedBetweenness: bts };
  }, [isSigmaMode, sigmaGraph]);

  // Synthetic Edge[] from Graphology edges (for inspection panel in sigma mode)
  const sigmaEdges = useMemo(() => {
    if (!isSigmaMode || !sigmaGraph) return [];
    const edges: Edge[] = [];
    sigmaGraph.forEachEdge((edgeKey, attrs, source, target) => {
      edges.push({
        id: edgeKey,
        source,
        target,
        type: "dependency",
        data: {
          importedNames: attrs.importedNames,
          edgeCount: attrs.edgeCount,
          confidence: attrs.confidence,
        },
      });
    });
    return edges;
  }, [isSigmaMode, sigmaGraph]);

  // Unified data accessors — select between React Flow and Sigma sources
  const effectiveNodeDataMap = isSigmaMode && sigmaDataMaps ? sigmaDataMaps.fileMap : allNodeDataMap;
  const effectiveModuleDataMap = isSigmaMode && sigmaDataMaps ? sigmaDataMaps.modMap : allModuleDataMap;
  const effectivePageranks = isSigmaMode && sigmaDataMaps ? sigmaDataMaps.sortedPageranks : sortedPageranks;
  const effectiveBetweenness = isSigmaMode && sigmaDataMaps ? sigmaDataMaps.sortedBetweenness : sortedBetweenness;
  const effectiveEdges = isSigmaMode ? sigmaEdges : currentEdges;

  // Community dimming
  const communityDimmedNodes = useMemo(() => {
    if (!activeCommunities) return null;
    const dimmed = new Set<string>();

    if (isSigmaMode && sigmaGraph) {
      sigmaGraph.forEachNode((nodeId, attrs) => {
        if (attrs.nodeType === "file" && !activeCommunities.has(attrs.communityId)) {
          dimmed.add(nodeId);
        }
      });
    } else {
      for (const n of filteredNodes) {
        if (n.type === "fileNode") {
          const cid = (n.data as { communityId?: number }).communityId;
          if (cid !== undefined && !activeCommunities.has(cid)) dimmed.add(n.id);
        }
      }
    }
    return dimmed.size > 0 ? dimmed : null;
  }, [activeCommunities, filteredNodes, isSigmaMode, sigmaGraph]);

  // Search
  const fuseIndex = useMemo(() => {
    if (isSigmaMode && sigmaGraph) {
      const items: { id: string; label: string }[] = [];
      sigmaGraph.forEachNode((id, attrs) => {
        if (hideTests && attrs.isTest) return;
        items.push({ id, label: attrs.label });
      });
      return new Fuse(items, { keys: ["id", "label"], threshold: 0.4 });
    }
    const items = filteredNodes.map((n) => ({ id: n.id, label: (n.data as { label?: string }).label ?? n.id }));
    return new Fuse(items, { keys: ["id", "label"], threshold: 0.4 });
  }, [isSigmaMode, sigmaGraph, filteredNodes, hideTests]);

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

    if (isSigmaMode && sigmaGraph) {
      sigmaGraph.forEachNode((nodeId) => {
        if (!matchIds.has(nodeId)) dimmed.add(nodeId);
      });
    } else {
      for (const n of filteredNodes) {
        if (!matchIds.has(n.id)) dimmed.add(n.id);
      }
    }

    setSearchDimmedNodes(dimmed);
    setSearchResults(ids);
    setSearchResultIndex(0);

    if (ids.length === 1) {
      setSelectedNodeId(ids[0]!);
    }

    if (ids.length > 0 && ids.length <= 20) {
      if (isSigmaMode) {
        sigmaRef.current?.focusNode(ids[0]!);
      } else {
        const matchedRfNodes = results
          .map((r) => reactFlow.getNode(r.item.id))
          .filter(Boolean) as Node[];
        if (matchedRfNodes.length > 0) {
          reactFlow.fitView({ nodes: matchedRfNodes, padding: 0.4, duration: 500 });
        }
      }
    }
  }, [searchQuery, fuseIndex, filteredNodes, reactFlow, isSigmaMode, sigmaGraph]);

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
      if (isSigmaMode) {
        const firstNode = flow.trace[0];
        if (firstNode) sigmaRef.current?.focusNode(firstNode);
      } else {
        const traceNodes = flow.trace
          .map((id) => reactFlow.getNode(id))
          .filter(Boolean) as Node[];
        if (traceNodes.length > 0) {
          reactFlow.fitView({ nodes: traceNodes, padding: 0.3, duration: 600 });
        }
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
      expandedModules,
      activeSignals,
    }),
    [highlightedPath, highlightedEdges, colorMode, viewMode, hoveredNodeId, connectedNodeIds, connectedEdgeIds, selectedNodeId, searchDimmedNodes, communityDimmedNodes, layoutMode, graphTheme, maxPagerank, medianPagerank, expandedModules, activeSignals],
  );

  // ---- Handlers ----

  const handleNodeClick: NodeMouseHandler = useCallback(
    (_event, node) => {
      if (colorMode === "community" && node.type === "fileNode") {
        const nodeData = node.data as FileNodeData;
        if (nodeData?.communityId !== undefined) {
          setCommunityPanelId(nodeData.communityId);
        }
      }

      if (selectedNodeId === node.id) {
        setSelectedNodeId(null);
      } else {
        setSelectedNodeId(node.id);
      }
    },
    [selectedNodeId, colorMode],
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
      if (node.type === "moduleGroup") {
        toggleModule(node.id);
      } else {
        onNodeViewDocs?.(node.id);
      }
    },
    [onNodeViewDocs, toggleModule],
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
    if (isSigmaMode) {
      sigmaRef.current?.fitView();
      return;
    }
    reactFlow.fitView({ padding: 0.15, duration: 400 });
  }, [reactFlow, isSigmaMode]);

  const handleViewChange = useCallback((v: ViewMode) => {
    setViewMode(v);
    setModulePath([]);
    setHighlightedPath(new Set());
    setHighlightedEdges(new Set());
    setSelectedNodeId(null);
    onViewModeChange?.(v);
  }, [onViewModeChange]);

  const handleLayoutModeChange = useCallback((mode: LayoutMode) => {
    setLayoutMode(mode);
    hasFocusedRef.current = false;
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

  const panToNode = useCallback((nodeId: string) => {
    if (isSigmaMode) {
      sigmaRef.current?.focusNode(nodeId);
      return;
    }
    const rfNode = reactFlow.getNode(nodeId);
    if (rfNode) {
      reactFlow.fitView({ nodes: [rfNode], padding: 1.5, duration: 400, maxZoom: 1.5 });
    }
  }, [reactFlow, isSigmaMode]);

  const initialNodeApplied = useRef(false);
  useEffect(() => {
    if (initialNodeApplied.current || !initialSelectedNode) return;
    const rfNode = reactFlow.getNode(initialSelectedNode);
    if (rfNode) {
      initialNodeApplied.current = true;
      setSelectedNodeId(initialSelectedNode);
      setTimeout(() => panToNode(initialSelectedNode), 300);
    }
  }, [initialSelectedNode, reactFlow, panToNode]);

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
    if (isSigmaMode && sigmaGraph) {
      sigmaGraph.forEachNode((_nodeId, attrs) => {
        if (attrs.nodeType === "file") ids.add(attrs.communityId);
      });
    } else {
      for (const n of filteredNodes) {
        if (n.type === "fileNode") {
          const cid = (n.data as { communityId?: number }).communityId;
          if (cid !== undefined) ids.add(cid);
        }
      }
    }
    return ids;
  }, [filteredNodes, isSigmaMode, sigmaGraph]);

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

  // Auto-fit after layout completes
  const hasFocusedRef = useRef(false);

  useEffect(() => {
    if (isLayouting || filteredNodes.length === 0 || hasFocusedRef.current) return;
    hasFocusedRef.current = true;

    const timer = setTimeout(() => {
      if (isModuleView) {
        reactFlow.fitView({ padding: 0.3, duration: 600, maxZoom: 1.2 });
      } else {
        reactFlow.fitView({ padding: 0.15, duration: 600, maxZoom: 0.6 });
      }
    }, 100);

    return () => clearTimeout(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isLayouting, filteredNodes, reactFlow]);

  useEffect(() => {
    hasFocusedRef.current = false;
  }, [viewMode, layoutMode]);

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

        {isSigmaMode ? (
          <SigmaCanvas
            ref={sigmaRef}
            graph={sigmaGraph}
            selectedNodeId={selectedNodeId}
            hoveredNodeId={hoveredNodeId}
            highlightedPath={highlightedPath}
            highlightedEdges={highlightedEdges}
            searchDimmedNodes={searchDimmedNodes}
            communityDimmedNodes={communityDimmedNodes}
            colorMode={colorMode}
            activeSignals={activeSignals}
            graphTheme={graphTheme}
            onNodeClick={handleSigmaNodeClick}
            onNodeDoubleClick={handleSigmaDoubleClick}
            onNodeHover={setHoveredNodeId}
            onNodeContextMenu={handleSigmaNodeContextMenu}
            onStageClick={() => setSelectedNodeId(null)}
          />
        ) : displayNodes.length === 0 && !isLayouting ? (
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
            onPaneClick={() => { setSelectedNodeId(null); }}
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
            onHideTestsChange={(v) => handleSignalToggle("hideTests")}
            onFitView={handleFitView}
            showPathFinder={showPathFinder}
            onTogglePathFinder={() => setShowPathFinder((s) => !s)}
            showFlows={showFlows}
            onToggleFlows={() => { setShowFlows((s) => !s); setActiveFlowIdx(null); }}
            searchQuery={searchQuery}
            onSearchChange={setSearchQuery}
            searchMatchCount={searchResults.length}
            searchTotalCount={isSigmaMode && sigmaGraph ? sigmaGraph.order : filteredNodes.length}
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
            nodeCount={isSigmaMode && sigmaGraph ? sigmaGraph.order : displayNodes.length}
            edgeCount={isSigmaMode && sigmaGraph ? sigmaGraph.size : currentEdges.length}
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
              edges={effectiveEdges}
              allNodes={effectiveNodeDataMap}
              allPageranks={effectivePageranks}
              allBetweenness={effectiveBetweenness}
              communityLabel={fileNd ? communityLabels?.get(fileNd.communityId) : undefined}
              onClose={() => { setSelectedNodeId(null); }}
              onNavigateToNode={handleInspectNavigate}
              onViewDocs={() => { onNodeViewDocs?.(selectedNodeId); }}
              onFindPath={handleInspectFindPath}
              onExpandModule={modNd ? handleInspectExpandModule : undefined}
            />
          );
        })()}
      </div>
    </GraphProvider>
  );
}

export function GraphFlow(props: GraphFlowProps) {
  return (
    <ReactFlowProvider>
      <GraphFlowInner {...props} />
    </ReactFlowProvider>
  );
}
