"use client";

import {
  useCallback,
  useMemo,
  useState,
  useEffect,
  useRef,
  type ReactNode,
} from "react";
import { useTheme } from "next-themes";
import { ChevronRight, Home } from "lucide-react";
import { Skeleton } from "../ui/skeleton";
import { EmptyState } from "../shared/empty-state";
import { GraphProvider, type GraphContextValue, type Signal } from "./context";
import { groupNodesAsModules, type FileNodeData, type ModuleNodeData } from "./elk-layout";

const EMPTY_STRING_SET = new Set<string>();
// Resting zoom when easing the camera onto a constellation hub. Looser than the
// default file-node focus (0.15) so the hub *and* its surrounding cluster stay
// visible instead of the disc filling the whole viewport.
const HUB_FOCUS_RATIO = 0.45;
// Below this node count file graphs build synchronously; at or above it we
// build in chunks off the critical path (see sigmaGraph below).
const ASYNC_BUILD_THRESHOLD = 1000;
import { useExpandedModules } from "./use-expanded-modules";
import { useExpandedHubs } from "./use-expanded-hubs";
import { traceToEdgeKeys } from "./graph-flow-helpers";
import { useGraphContextMenu } from "./use-graph-context-menu";
import { useGraphSearch } from "./use-graph-search";
import { useCommunityFilter } from "./use-community-filter";
import { useGraphKeyboardShortcuts } from "./use-graph-keyboard-shortcuts";
import { GraphToolbar, type ColorMode, type ViewMode, type LayoutMode, type GraphTheme } from "./graph-toolbar";
import { GraphLegend } from "./graph-legend";
import { GraphContextMenu } from "./graph-context-menu";
import { GraphInspectionPanel } from "./graph-inspection-panel";
import type {
  GraphExport,
  ModuleGraph,
  ExecutionFlows,
  CommunitySummaryItem,
  ArchitectureGraph,
  CommunitySlice,
} from "@repowise-dev/types/graph";
import { SigmaCanvas, type SigmaCanvasHandle } from "./sigma/sigma-canvas";
import {
  fileGraphToGraphology,
  fileGraphToGraphologyAsync,
  moduleGraphToGraphology,
  groupFilesAsModules,
} from "./sigma/graphology-adapter";
import {
  architectureToGraphology,
  hubNodeId,
  mergeCommunitySlice,
} from "./sigma/constellation-adapter";
import { computeRadialLayout } from "./sigma/radial-layout";
import type { SigmaNodeAttributes, SigmaEdgeAttributes } from "./sigma/types";
import type GraphologyGraph from "graphology";
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
  /** Community super-graph for the constellation (radial Knowledge Graph) scope. */
  constellationGraph?: ArchitectureGraph | undefined;
  isLoadingConstellationGraph?: boolean;
  /** Member slices for currently-expanded hubs, keyed by community_id. The host
   *  fetches these in response to {@link onExpandedHubsChange}. */
  constellationSlices?: Map<number, CommunitySlice> | undefined;
  /** Fired when the set of expanded constellation hubs changes, so the host can
   *  fetch the corresponding slices. */
  onExpandedHubsChange?: (expanded: number[]) => void;
  /** Repo name for the constellation core label. */
  repoName?: string;
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
    /** Blossom this community's files on the canvas (expand affordance). */
    onExpandOnCanvas: () => void;
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
    constellationGraph,
    isLoadingConstellationGraph,
    constellationSlices,
    onExpandedHubsChange,
    repoName,
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
  // Default scope is the constellation (radial Knowledge Graph).
  const [viewMode, setViewMode] = useState<ViewMode>(initialViewMode ?? "architecture");
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
  // Constellation is the default scope → its fixed radial layout.
  const [layoutMode, setLayoutMode] = useState<LayoutMode>(
    (initialViewMode ?? "architecture") === "architecture" ? "radial" : "force",
  );

  // The dependency graph follows the global app theme rather than a separate
  // local toggle. Sigma needs a concrete "light"/"dark" (never "system"), so
  // resolve it; the in-graph Sun/Moon control flips the global theme below.
  const { resolvedTheme, setTheme } = useTheme();
  const graphTheme: GraphTheme = resolvedTheme === "dark" ? "dark" : "light";

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

  // Expand/collapse constellation hubs (radial blossom). Esc collapses the most
  // recently expanded hub; multiple hubs may be open at once.
  const { expandedHubs, toggleHub, collapseLast, collapseAll: collapseAllHubs } =
    useExpandedHubs();

  useEffect(() => {
    onExpandedHubsChange?.(expandedHubs);
  }, [expandedHubs, onExpandedHubsChange]);

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

  // Context menu (state + dismiss-on-click/Escape lifecycle)
  const { ctxMenu, setCtxMenu } = useGraphContextMenu();

  // Path finder pre-fill
  const [pathFrom, setPathFrom] = useState("");
  const [pathTo, setPathTo] = useState("");

  // Hover & selection
  const [hoveredNodeId, setHoveredNodeId] = useState<string | null>(null);
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);

  // Execution flows
  const [activeFlowIdx, setActiveFlowIdx] = useState<number | null>(null);
  const [showFlows, setShowFlows] = useState(false);

  // Community detail panel (the filter state itself lives in useCommunityFilter)
  const [communityPanelId, setCommunityPanelId] = useState<number | null>(null);

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

  // Legend click in the constellation: select the hub, ease the camera onto it,
  // and surface the community in the detail panel — NO expand. This mirrors the
  // unified single-click grammar (expansion is reserved for double-click).
  const handleConstellationHubClick = useCallback(
    (cid: number) => {
      const nodeId = hubNodeId(cid);
      setSelectedNodeId(nodeId);
      sigmaRef.current?.focusNode(nodeId, HUB_FOCUS_RATIO);
      openCommunityPanel(cid);
    },
    [openCommunityPanel],
  );

  // Hub double-click toggles the blossom: expand eases the camera to frame the
  // cluster (and opens the panel); collapse just folds it back.
  const handleConstellationHubToggle = useCallback(
    (cid: number) => {
      const willExpand = !expandedHubs.includes(cid);
      toggleHub(cid);
      if (willExpand) {
        const nodeId = hubNodeId(cid);
        setSelectedNodeId(nodeId);
        sigmaRef.current?.focusNode(nodeId, HUB_FOCUS_RATIO);
        openCommunityPanel(cid);
      }
    },
    [expandedHubs, toggleHub, openCommunityPanel],
  );

  // ---- Derived state ----
  const isModuleView = viewMode === "module";
  const isUnified = viewMode === "unified";
  // The "architecture" scope renders the radial community constellation.
  const isConstellation = viewMode === "architecture";

  // Constellation graph: one hub per community + repo-core, radial positions.
  // When hubs are expanded, blossom each one's member slice as satellites
  // (deterministic radial math, no FA2). Rebuilt fresh each time (the adapter
  // is pure + the merge mutates only the new instance), so the memo stays pure.
  const constellationSigmaGraph = useMemo(() => {
    if (!isConstellation || !constellationGraph) return null;
    const graph = architectureToGraphology(
      constellationGraph,
      repoName ? { repoName } : {},
    );
    if (constellationSlices) {
      for (const cid of expandedHubs) {
        const slice = constellationSlices.get(cid);
        if (slice) mergeCommunitySlice(graph, cid, slice);
      }
    }
    return graph;
  }, [isConstellation, constellationGraph, repoName, expandedHubs, constellationSlices]);

  // Nodes to dim while any hub is expanded: every hub disc NOT in the expanded
  // set (and the repo-core), so the open cluster(s) read as foreground. Reuses
  // the dimColor machinery via the dedicated expand-dim channel.
  const expandDimmedNodes = useMemo(() => {
    if (!isConstellation || expandedHubs.length === 0 || !constellationGraph) {
      return null;
    }
    const expandedSet = new Set(expandedHubs);
    const dimmed = new Set<string>();
    for (const n of constellationGraph.nodes) {
      if (!expandedSet.has(n.community_id)) dimmed.add(hubNodeId(n.community_id));
    }
    return dimmed;
  }, [isConstellation, expandedHubs, constellationGraph]);

  // Ring radii for the depth-ring underlay (graph coordinates).
  const constellationRingRadii = useMemo(() => {
    if (!isConstellation || !constellationGraph) return null;
    return computeRadialLayout(
      constellationGraph.nodes.map((n) => ({
        community_id: n.community_id,
        member_count: n.member_count,
        avg_pagerank: n.avg_pagerank,
      })),
    ).ringRadii;
  }, [isConstellation, constellationGraph]);

  const communityLabels = useMemo(() => {
    if (!communities) return undefined;
    const m = new Map<number, string>();
    for (const c of communities) m.set(c.community_id, c.label);
    return m;
  }, [communities]);

  // Constellation legend data: label + member count per community, ranked by
  // size, sourced from the architecture payload (independent of /communities).
  const constellationLegend = useMemo(() => {
    if (!constellationGraph) return undefined;
    return [...constellationGraph.nodes]
      .sort((a, b) => b.member_count - a.member_count)
      .map((n) => ({
        communityId: n.community_id,
        label: (n.label || `Community ${n.community_id}`),
        memberCount: n.member_count,
      }));
  }, [constellationGraph]);

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
      // "architecture" now renders the radial constellation, not a file graph.
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
  }, [viewMode, fullGraph, deadCodeGraph, hotFilesGraph]);

  // Loading state
  const isLoading =
    (isModuleView && !isDrilledDown) ? isLoadingModuleGraph :
    isDrilledDown ? isLoadingFullGraph :
    viewMode === "full" || viewMode === "unified" ? isLoadingFullGraph :
    viewMode === "architecture" ? !!isLoadingConstellationGraph :
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

  // Build Graphology graph for Sigma rendering.
  //
  // Module / drill-down / small file graphs build synchronously here. Large
  // file graphs (>= ASYNC_BUILD_THRESHOLD) are deferred off the critical path:
  // this memo returns null and the effect below constructs them in chunks,
  // keeping the loading state up until the first frame is ready.
  const syncSigmaGraph = useMemo(() => {
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

    // Defer large file graphs to the async effect below.
    if (graphData.nodes.length >= ASYNC_BUILD_THRESHOLD) return null;

    const signals: { hotNodeIds?: Set<string>; deadNodeIds?: Set<string> } = {};
    if (hasHotSignal || isUnified) signals.hotNodeIds = hotNodeIds;
    if (hasDeadSignal || isUnified) signals.deadNodeIds = deadNodeIds;

    return fileGraphToGraphology(
      { nodes: graphData.nodes, links: graphData.links },
      { signals },
    );
  }, [isModuleView, isDrilledDown, fullGraph, currentPrefix, moduleGraph, communities, expandedModules, fileGraphData, hasHotSignal, hasDeadSignal, isUnified, hotNodeIds, deadNodeIds, fullGraphIndexes]);

  // Async-built file graph for large graphs (built in chunks off the main
  // thread critical path). Null while building / when the sync path applies.
  const [asyncSigmaGraph, setAsyncSigmaGraph] = useState<GraphologyGraph<
    SigmaNodeAttributes,
    SigmaEdgeAttributes
  > | null>(null);
  const [isBuildingGraph, setIsBuildingGraph] = useState(false);

  const needsAsyncBuild =
    !isModuleView &&
    !!fileGraphData &&
    fileGraphData.nodes.length >= ASYNC_BUILD_THRESHOLD;

  useEffect(() => {
    if (!needsAsyncBuild || !fileGraphData) {
      setAsyncSigmaGraph(null);
      setIsBuildingGraph(false);
      return;
    }

    let cancelled = false;
    setIsBuildingGraph(true);

    const signals: { hotNodeIds?: Set<string>; deadNodeIds?: Set<string> } = {};
    if (hasHotSignal || isUnified) signals.hotNodeIds = hotNodeIds;
    if (hasDeadSignal || isUnified) signals.deadNodeIds = deadNodeIds;

    void fileGraphToGraphologyAsync(
      { nodes: fileGraphData.nodes, links: fileGraphData.links },
      { signals },
    ).then((graph) => {
      if (cancelled) return;
      setAsyncSigmaGraph(graph);
      setIsBuildingGraph(false);
    });

    return () => {
      cancelled = true;
    };
  }, [needsAsyncBuild, fileGraphData, hasHotSignal, hasDeadSignal, isUnified, hotNodeIds, deadNodeIds]);

  const sigmaGraph = isConstellation
    ? constellationSigmaGraph
    : (syncSigmaGraph ?? asyncSigmaGraph);

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

  const panToNode = useCallback((nodeId: string) => {
    sigmaRef.current?.focusNode(nodeId);
  }, []);

  // Search (Fuse index + debounced query + result navigation)
  const { searchQuery, setSearchQuery, searchResults, searchDimmedNodes, handleSearchKeyDown } =
    useGraphSearch({ sigmaGraph, hideTests, panToNode, setSelectedNodeId });

  // Community filter (active communities + dimming + legend toggles)
  const { activeCommunities, communityDimmedNodes, handleCommunityToggle, handleToggleAllCommunities } =
    useCommunityFilter(sigmaGraph);

  // Switch from the module overview into the flat file view. Shared by the
  // execution-flow highlighter and the path-finder result handler, which both
  // need a file-level graph before they can highlight a trace.
  const enterFullViewFromModule = useCallback(() => {
    if (viewMode === "module") {
      setViewMode("full");
      setModulePath([]);
      onViewModeChange?.("full");
    }
  }, [viewMode, onViewModeChange]);

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
    setHighlightedPath(new Set(flow.trace));
    setHighlightedEdges(traceToEdgeKeys(flow.trace));

    enterFullViewFromModule();

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

  // Unified grammar — DOUBLE CLICK = drill deeper (all views):
  //   module    → expand/collapse the module's children
  //   hub       → toggle the radial blossom (expand eases the camera onto it)
  //   file/sat. → open the doc panel
  //   core      → no-op (Sigma's default camera zoom is allowed)
  // Returns true when an action ran so the canvas suppresses Sigma's default
  // double-click zoom; core returns void so the zoom-jump is kept.
  const handleSigmaDoubleClick = useCallback(
    (nodeId: string, nodeType: string): boolean | void => {
      if (nodeType === "module") {
        toggleModule(nodeId);
        return true;
      }
      if (nodeType === "hub" && sigmaGraph?.hasNode(nodeId)) {
        const cid = sigmaGraph.getNodeAttribute(nodeId, "communityId");
        if (typeof cid === "number" && cid >= 0) {
          handleConstellationHubToggle(cid);
          return true;
        }
        return;
      }
      if (nodeType === "core") return;
      onNodeViewDocs?.(nodeId);
      return true;
    },
    [onNodeViewDocs, toggleModule, sigmaGraph, handleConstellationHubToggle],
  );

  // Unified grammar — SINGLE CLICK = select + inspect (never structural):
  //   file/module → select (no expansion; drill-down moved to double-click)
  //   hub         → select + focus + open the community panel (NO expand)
  //   core        → no-op
  // Clicking an already-selected node is a no-op (keeps it selected); the two
  // pre-clicks Sigma fires before a double-click therefore can't churn the
  // selection. Deselection happens via stage click or Esc.
  const handleSigmaNodeClick = useCallback(
    (nodeId: string, nodeType: string) => {
      if (nodeType === "core") return;
      if (selectedNodeId === nodeId) return;
      if (nodeType === "hub" && sigmaGraph?.hasNode(nodeId)) {
        const cid = sigmaGraph.getNodeAttribute(nodeId, "communityId");
        if (typeof cid === "number" && cid >= 0) {
          setSelectedNodeId(nodeId);
          sigmaRef.current?.focusNode(nodeId, HUB_FOCUS_RATIO);
          openCommunityPanel(cid);
          return;
        }
      }
      setSelectedNodeId(nodeId);
    },
    [selectedNodeId, sigmaGraph, openCommunityPanel],
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
    [setCtxMenu],
  );

  // Esc dismisses the top UI layer first (unified grammar): clear an open
  // selection/panel before collapsing a constellation hub. Each press peels one
  // layer; the keyboard hook's default clear only runs once nothing is open.
  //   1. node selected OR community panel open → clear selection + panel + ego
  //   2. else any hub expanded → collapse the most recent
  //   3. else → fall through to the default clear (search, ctx menu, …)
  const handleEscapeCollapse = useCallback((): boolean => {
    if (selectedNodeId !== null || communityPanelId !== null) {
      setSelectedNodeId(null);
      setCommunityPanelId(null);
      setEgoDepth(0);
      return true;
    }
    if (isConstellation && expandedHubs.length > 0) {
      collapseLast();
      return true;
    }
    return false;
  }, [selectedNodeId, communityPanelId, isConstellation, expandedHubs.length, collapseLast]);

  // Global keyboard shortcuts (f/Escape/1-3//, cmd+k)
  useGraphKeyboardShortcuts({
    sigmaRef,
    setSelectedNodeId,
    setEgoDepth,
    setSearchQuery,
    setCtxMenu,
    setCommunityPanelId,
    setColorMode,
    onEscape: handleEscapeCollapse,
  });

  const handlePathFound = useCallback(
    (pathNodes: string[]) => {
      setHighlightedPath(new Set(pathNodes));
      setHighlightedEdges(traceToEdgeKeys(pathNodes));
      enterFullViewFromModule();
      clearTimeout(focusTimerRef.current);
      focusTimerRef.current = setTimeout(() => {
        if (pathNodes.length > 0) {
          sigmaRef.current?.focusNode(pathNodes[0]!);
        }
      }, 800);
    },
    [enterFullViewFromModule],
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
    // Constellation is fixed-radial; other scopes default back to FA2.
    setLayoutMode(v === "architecture" ? "radial" : "force");
    setModulePath([]);
    setHighlightedPath(new Set());
    setHighlightedEdges(new Set());
    setSelectedNodeId(null);
    // Leaving the constellation collapses any open blossoms.
    if (v !== "architecture") collapseAllHubs();
    onViewModeChange?.(v);
  }, [onViewModeChange, collapseAllHubs]);

  const handleLayoutModeChange = useCallback((mode: LayoutMode) => {
    setLayoutMode(mode);
  }, []);

  const handleGraphThemeChange = useCallback(
    (theme: GraphTheme) => {
      // Drive the global theme; the graph re-reads it via resolvedTheme.
      setTheme(theme);
    },
    [setTheme],
  );

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

  const initialNodeApplied = useRef(false);
  useEffect(() => {
    if (initialNodeApplied.current || !initialSelectedNode || !sigmaGraph) return;
    if (sigmaGraph.hasNode(initialSelectedNode)) {
      initialNodeApplied.current = true;
      setSelectedNodeId(initialSelectedNode);
      setTimeout(() => panToNode(initialSelectedNode), 300);
    }
  }, [initialSelectedNode, sigmaGraph, panToNode]);

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
  }, [ctxMenu, onNodeViewDocs, setCtxMenu]);

  const handleCtxExplore = useCallback(() => {
    if (ctxMenu) {
      if (ctxMenu.nodeType === "moduleGroup" && isModuleView) {
        setModulePath((prev) => [...prev, ctxMenu.nodeId]);
      } else {
        onNodeClick?.(ctxMenu.nodeId, ctxMenu.nodeType);
      }
    }
    setCtxMenu(null);
  }, [ctxMenu, isModuleView, onNodeClick, setCtxMenu]);

  const handleCtxPathFrom = useCallback(() => {
    if (ctxMenu) { setPathFrom(ctxMenu.nodeId); setShowPathFinder(true); }
    setCtxMenu(null);
  }, [ctxMenu, setCtxMenu]);

  const handleCtxPathTo = useCallback(() => {
    if (ctxMenu) { setPathTo(ctxMenu.nodeId); setShowPathFinder(true); }
    setCtxMenu(null);
  }, [ctxMenu, setCtxMenu]);

  if (isLoading || (isBuildingGraph && !sigmaGraph))
    return <Skeleton className="h-full w-full rounded-lg" />;

  return (
    <GraphProvider value={ctxValue}>
      <div className="relative w-full h-full" style={{ touchAction: "none", ...(graphTheme === "dark" ? { background: "var(--color-bg-inset)" } : {}) }} aria-label="Dependency graph">
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
            expandDimmedNodes={isConstellation ? expandDimmedNodes : null}
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
            depthRingRadii={isConstellation ? constellationRingRadii : null}
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
            visibleEdgeTypes={isConstellation ? undefined : visibleEdgeTypes}
            onEdgeTypeToggle={isConstellation ? undefined : handleEdgeTypeToggle}
            graphTheme={graphTheme}
            constellationEntries={isConstellation ? constellationLegend : undefined}
            onConstellationHubClick={handleConstellationHubClick}
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
            onExpandOnCanvas: () => handleConstellationHubToggle(communityPanelId),
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
