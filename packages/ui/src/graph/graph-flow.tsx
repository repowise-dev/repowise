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
import { X } from "lucide-react";
import { Skeleton } from "../ui/skeleton";
import { EmptyState } from "../shared/empty-state";
import { type Signal } from "./context";
import { type FileNodeData, type ModuleNodeData } from "./elk-layout";

// Resting zoom when easing the camera onto a constellation hub. Looser than the
// default file-node focus (0.15) so the hub *and* its surrounding cluster stay
// visible instead of the disc filling the whole viewport.
const HUB_FOCUS_RATIO = 0.45;
// Below this node count file graphs build synchronously; at or above it we
// build in chunks off the critical path (see sigmaGraph below).
const ASYNC_BUILD_THRESHOLD = 1000;

import { useExpandedHubs } from "./use-expanded-hubs";
import { traceToEdgeKeys, traceToFileTrace } from "./graph-flow-helpers";
import { useGraphContextMenu } from "./use-graph-context-menu";
import { useGraphSearch } from "./use-graph-search";
import { useCommunityFilter } from "./use-community-filter";
import { useModuleFilter } from "./use-module-filter";
import { useGraphKeyboardShortcuts } from "./use-graph-keyboard-shortcuts";
import { GraphToolbar, type ColorMode, type ViewMode, type LayoutMode, type GraphTheme } from "./graph-toolbar";
import { GraphLegend } from "./graph-legend";
import { GraphContextMenu } from "./graph-context-menu";
import { GraphInspectionPanel } from "./graph-inspection-panel";
import { GraphShortcutHelp } from "./graph-shortcut-help";
import type {
  GraphExport,
  ExecutionFlows,
  CommunitySummaryItem,
  ArchitectureGraph,
  CommunitySlice,
} from "@repowise-dev/types/graph";
import { SigmaCanvas, type SigmaCanvasHandle } from "./sigma/sigma-canvas";
import {
  fileGraphToGraphology,
  fileGraphToGraphologyAsync,
} from "./sigma/graphology-adapter";
import {
  architectureToGraphology,
  hubNodeId,
  mergeCommunitySlice,
} from "./sigma/constellation-adapter";
import { computeRadialLayout } from "./sigma/radial-layout";
import { ELK_MAX_NODES, elkSkipReason } from "./sigma/use-elk-sigma-layout";
import type { SigmaNodeAttributes, SigmaEdgeAttributes } from "./sigma/types";
import type GraphologyGraph from "graphology";
import { useEgoFilter } from "./sigma/use-ego-filter";

export interface GraphFlowProps {
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
  /** Controlled scope. Scope is now steered from the page's section header
   *  (`GraphScopeSwitcher`) rather than from a pill cluster on the canvas, so
   *  the host owns the value and URL-syncs it. Omit to let the component track
   *  its own, seeded by {@link initialViewMode}. */
  viewMode?: ViewMode;
  /** The module filter's current selection (a path prefix from
   *  `moduleGroupFor`), or null for "all modules". Controlled by the host so
   *  the control can live in the section header beside the scope switcher. */
  activeModule?: string | null;
  /** Fired with the module groups present in the rendered graph, so the host
   *  can populate its filter control. Counts are of nodes actually drawn. */
  onModuleGroupsChange?: (groups: { id: string; fileCount: number }[]) => void;
  /** Initial node color mode (uncontrolled seed). Hosts derive this from their
   *  URL state instead of the component reading window.location. Ignored when
   *  {@link colorMode} is supplied. */
  initialColorMode?: ColorMode;
  /** Controlled node color mode. When supplied, the host owns the value (and
   *  typically URL-syncs it); the component reflects it directly and reports
   *  user changes via {@link onColorModeChange}. Omit to let the component
   *  track its own color mode seeded by {@link initialColorMode}. */
  colorMode?: ColorMode;
  initialSelectedNode?: string | null;
  onViewModeChange?: (mode: ViewMode) => void;
  /** Fired when the node color mode changes (toolbar or 1/2/3 shortcut) so
   *  hosts can sync it to the URL. */
  onColorModeChange?: (mode: ColorMode) => void;
  onNodeClick?: (nodeId: string, nodeType: string) => void | Promise<void>;
  onNodeViewDocs?: (nodeId: string) => void;
  /** "Symbols" action in the inspection panel — jump to the symbols view
   *  filtered to the selected file. */
  onNodeViewSymbols?: (nodeId: string) => void;
  /** Canonical file-page href for a file node — renders an "Open file page"
   *  action in the inspection panel. */
  fileHrefFor?: (nodeId: string) => string;
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
    viewMode: controlledViewMode,
    activeModule: controlledActiveModule,
    onModuleGroupsChange,
    initialColorMode,
    colorMode: controlledColorMode,
    initialSelectedNode,
    onViewModeChange,
    onColorModeChange,
    onNodeClick,
    onNodeViewDocs,
    onNodeViewSymbols,
    fileHrefFor,
    renderPathFinder,
    renderCommunityPanel,
    onCommunityPanelOpen,
  } = props;

  const sigmaRef = useRef<SigmaCanvasHandle>(null);
  const focusTimerRef = useRef<ReturnType<typeof setTimeout>>(undefined);

  // ---- Core state ----
  // Default scope is the constellation (radial Knowledge Graph). Controlled by
  // the host when `viewMode` is supplied — the scope switcher lives in the
  // page's section header now, so the URL is the source of truth.
  const [viewModeState, setViewModeState] = useState<ViewMode>(
    initialViewMode ?? "architecture",
  );
  const viewMode = controlledViewMode ?? viewModeState;
  // Color mode is controlled by the host when `colorMode` is supplied
  // (URL-synced); otherwise the component tracks it locally, seeded by
  // `initialColorMode`. The wrapped setter routes through the host callback in
  // controlled mode and falls back to local state in uncontrolled mode.
  const [colorModeState, setColorModeState] = useState<ColorMode>(
    initialColorMode ?? "community",
  );
  const colorMode = controlledColorMode ?? colorModeState;
  const setColorMode = useCallback(
    (next: ColorMode) => {
      if (onColorModeChange) onColorModeChange(next);
      else setColorModeState(next);
    },
    [onColorModeChange],
  );
  const [highlightedPath, setHighlightedPath] = useState<Set<string>>(new Set());
  const [highlightedEdges, setHighlightedEdges] = useState<Set<string>>(new Set());
  const [showPathFinder, setShowPathFinder] = useState(false);
  const [showShortcutHelp, setShowShortcutHelp] = useState(false);
  // Explanation surfaced when the hierarchical layout refuses to run (too
  // many nodes) — otherwise the toggle looks active but does nothing.
  const [layoutNotice, setLayoutNotice] = useState<string | null>(null);
  // Constellation is the default scope → its fixed radial layout.
  const [layoutMode, setLayoutMode] = useState<LayoutMode>(
    (initialViewMode ?? "architecture") === "architecture" ? "radial" : "force",
  );

  // The dependency graph follows the global app theme rather than a separate
  // local toggle. Sigma needs a concrete "light"/"dark" (never "system"), so
  // resolve it. The toolbar used to carry its own Sun/Moon that just called
  // `setTheme` — a duplicate of the app's header toggle — and it is gone.
  const { resolvedTheme } = useTheme();
  const graphTheme: GraphTheme = resolvedTheme === "dark" ? "dark" : "light";

  const [egoDepth, setEgoDepth] = useState(0);

  const [visibleEdgeTypes, setVisibleEdgeTypes] = useState<Set<string>>(
    () => new Set(["import", "crossCommunity"]),
  );

  // Signal overlays (replaces separate view modes for dead/hot/arch).
  // Derived from the host-provided initial view mode — no URL reads here.
  const [activeSignals, setActiveSignals] = useState<Set<Signal>>(() => {
    if (initialViewMode === "dead") return new Set<Signal>(["dead"]);
    if (initialViewMode === "hotfiles") return new Set<Signal>(["hot"]);
    if (initialViewMode === "unified") return new Set<Signal>(["dead", "hot"]);
    return new Set<Signal>();
  });
  const hideTests = activeSignals.has("hideTests");

  // Expand/collapse constellation hubs (radial blossom). Esc collapses the most
  // recently expanded hub; multiple hubs may be open at once.
  const { expandedHubs, toggleHub, collapseLast, collapseAll: collapseAllHubs } =
    useExpandedHubs();

  useEffect(() => {
    onExpandedHubsChange?.(expandedHubs);
  }, [expandedHubs, onExpandedHubsChange]);

  // Context menu (state + dismiss-on-click/Escape lifecycle)
  const { ctxMenu, setCtxMenu } = useGraphContextMenu();

  // Path finder pre-fill
  const [pathFrom, setPathFrom] = useState("");
  const [pathTo, setPathTo] = useState("");

  // Selection. There is deliberately no hover state: Sigma draws its own hover
  // highlight on the canvas, so mirroring it into React only re-rendered this
  // whole shell on every hover transition for nothing.
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

  // File-level graph data for each scope
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

  // Repo-wide signal totals, when the backend provides them (the overlay's
  // own payload wins over the capped full graph). Distinguishes "the repo has
  // none" from "none survived the node cap" in the empty states below.
  const deadTotal =
    deadCodeGraph?.dead_total ?? fullGraph?.dead_total ?? null;
  const hotTotal = hotFilesGraph?.hot_total ?? fullGraph?.hot_total ?? null;

  // Build Graphology graph for Sigma rendering.
  //
  // Small file graphs build synchronously here. Large ones
  // (>= ASYNC_BUILD_THRESHOLD) are deferred off the critical path: this memo
  // returns null and the effect below constructs them in chunks, keeping the
  // loading state up until the first frame is ready.
  const syncSigmaGraph = useMemo(() => {
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
  }, [fileGraphData, hasHotSignal, hasDeadSignal, isUnified, hotNodeIds, deadNodeIds]);

  // Async-built file graph for large graphs (built in chunks off the main
  // thread critical path). Null while building / when the sync path applies.
  const [asyncSigmaGraph, setAsyncSigmaGraph] = useState<GraphologyGraph<
    SigmaNodeAttributes,
    SigmaEdgeAttributes
  > | null>(null);
  const [isBuildingGraph, setIsBuildingGraph] = useState(false);

  const needsAsyncBuild =
    !!fileGraphData && fileGraphData.nodes.length >= ASYNC_BUILD_THRESHOLD;

  // `isBuildingGraph` is only raised *inside* the effect below, which React
  // runs after it has already painted. So on the commit where an async build
  // first becomes necessary — the fetch has landed, the build has not started
  // — every loading flag reads false while `sigmaGraph` is still null, and the
  // canvas paints its "No graph data" empty state for a frame. Deriving the
  // wait during render closes the gap: any repo above ASYNC_BUILD_THRESHOLD
  // (1,000 nodes) hit this on every full / dead / hot load.
  const isAwaitingAsyncBuild = needsAsyncBuild && !asyncSigmaGraph;

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
          hotspotCount: attrs.hotspotCount ?? 0,
          deadCount: attrs.deadCount ?? 0,
          hasDecision: attrs.hasDecision ?? false,
          primaryOwner: attrs.primaryOwner ?? null,
          dominantCommunityId: attrs.dominantCommunityId,
        });
      }
    });

    return { fileMap, modMap };
  }, [sigmaGraph]);

  const effectiveNodeDataMap = sigmaNodeMaps?.fileMap ?? new Map<string, FileNodeData>();
  const effectiveModuleDataMap = sigmaNodeMaps?.modMap ?? new Map<string, ModuleNodeData>();

  // How many flagged nodes actually made it into the rendered graph — paired
  // with the repo-wide totals to caption the dead/hot views honestly.
  const overlayStats = useMemo(() => {
    if (!sigmaGraph) return null;
    let deadInView = 0;
    let hotInView = 0;
    sigmaGraph.forEachNode((_, attrs) => {
      if (attrs.isDead) deadInView++;
      if (attrs.isHotspot) hotInView++;
    });
    return { deadInView, hotInView };
  }, [sigmaGraph]);

  const isDeadView = viewMode === "dead" || viewMode === "unified";
  const isHotView = viewMode === "hotfiles" || viewMode === "unified";

  // Trace nodes of the selected execution flow that fell outside the loaded
  // node set — highlighting/focus silently no-op for them, so tell the user.
  const activeFlowMissingCount = useMemo(() => {
    if (activeFlowIdx === null || !executionFlows || !sigmaGraph) return 0;
    const flow = executionFlows.flows[activeFlowIdx];
    if (!flow) return 0;
    return traceToFileTrace(flow.trace).filter((id) => !sigmaGraph.hasNode(id)).length;
  }, [activeFlowIdx, executionFlows, sigmaGraph]);

  // Empty-state copy for a dead/hot view that resolved to zero nodes. Two
  // different failure modes deserve two different messages: the repo really
  // has no flagged files, vs the flagged files exist but fell outside the
  // capped node selection.
  const overlayEmptyState = (() => {
    if (!isDeadView && !isHotView) return null;
    const kind = isDeadView && isHotView ? "dead or hot" : isDeadView ? "dead" : "hot";
    const total = isDeadView && isHotView ? null : isDeadView ? deadTotal : hotTotal;
    if (total === 0) {
      return {
        title: `No ${kind} files in this repo`,
        description:
          kind === "dead"
            ? "No open dead-code findings — nothing to overlay."
            : "No files are flagged as hotspots — nothing to overlay.",
      };
    }
    if (total != null && total > 0) {
      return {
        title: `${kind === "dead" ? "Dead" : "Hot"} files are outside the loaded view`,
        description: `None of the ${total} ${kind} files are in the loaded node set. Load more nodes from the banner, or narrow the scope to bring them in.`,
      };
    }
    return {
      title: `No ${kind} files in this view`,
      description:
        "The repo may have none, or they may fall outside the loaded node set.",
    };
  })();

  const panToNode = useCallback((nodeId: string) => {
    sigmaRef.current?.focusNode(nodeId);
  }, []);

  // Search (Fuse index + debounced query + result navigation)
  const { searchQuery, setSearchQuery, searchResults, searchDimmedNodes, handleSearchKeyDown } =
    useGraphSearch({ sigmaGraph, hideTests, panToNode, setSelectedNodeId });

  // Community filter (active communities + dimming + legend toggles)
  const { activeCommunities, communityDimmedNodes, handleCommunityToggle, handleToggleAllCommunities } =
    useCommunityFilter(sigmaGraph);

  // Module filter (path-prefix dimming). Replaces the old Modules *scope*: the
  // control lives in the section header and the host owns the selection, so
  // only the dimming derivation happens here.
  const { moduleGroups, moduleDimmedNodes, handleModuleChange } = useModuleFilter(sigmaGraph);
  useEffect(() => {
    handleModuleChange(controlledActiveModule ?? null);
  }, [controlledActiveModule, handleModuleChange]);
  useEffect(() => {
    onModuleGroupsChange?.(moduleGroups);
  }, [moduleGroups, onModuleGroupsChange]);

  // The module filter and the community filter answer the same question — "is
  // this node outside what I asked for?" — so they share the one dim channel
  // and compose as an AND. Two independent dim levels would just muddy the
  // canvas with three shades of "not this".
  const filterDimmedNodes = useMemo(() => {
    if (!communityDimmedNodes) return moduleDimmedNodes;
    if (!moduleDimmedNodes) return communityDimmedNodes;
    const union = new Set(communityDimmedNodes);
    for (const id of moduleDimmedNodes) union.add(id);
    return union;
  }, [communityDimmedNodes, moduleDimmedNodes]);

  // Flow index whose trace head has already been focused, so the deferred
  // re-focus below fires at most once per selection and never re-steers the
  // camera on later graph changes while the same flow stays active.
  const flowFocusedRef = useRef<number | null>(null);
  // Live graph handle for the focus timer (the effect below deliberately
  // keeps sigmaGraph out of its deps).
  const sigmaGraphRef = useRef(sigmaGraph);
  sigmaGraphRef.current = sigmaGraph;

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
    const fileTrace = traceToFileTrace(flow.trace);
    setHighlightedPath(new Set(fileTrace));
    setHighlightedEdges(traceToEdgeKeys(fileTrace));

    clearTimeout(focusTimerRef.current);
    focusTimerRef.current = setTimeout(() => {
      focusTimerRef.current = undefined;
      const firstNode = fileTrace[0];
      if (!firstNode) return;
      if (sigmaGraphRef.current?.hasNode(firstNode)) {
        flowFocusedRef.current = activeFlowIdx;
        sigmaRef.current?.focusNode(firstNode);
      }
      // Node not loaded yet (module → full jump still fetching): the
      // deferred-focus effect below picks it up once the graph gains it.
    }, 800);
    return () => clearTimeout(focusTimerRef.current);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeFlowIdx, executionFlows]);

  // Deferred flow focus: selecting a flow from the module overview kicks off
  // the full-graph fetch, which can land after the 800ms timer above already
  // fired against a graph without the trace head. Focus once when the graph
  // gains the node; while the timer is still pending it stays the fast path.
  useEffect(() => {
    if (activeFlowIdx === null) {
      flowFocusedRef.current = null;
      return;
    }
    if (flowFocusedRef.current === activeFlowIdx) return;
    if (focusTimerRef.current !== undefined) return;
    const trace = executionFlows?.flows[activeFlowIdx]?.trace;
    const firstNode = trace ? traceToFileTrace(trace)[0] : undefined;
    if (!firstNode || !sigmaGraph?.hasNode(firstNode)) return;
    flowFocusedRef.current = activeFlowIdx;
    sigmaRef.current?.focusNode(firstNode);
  }, [activeFlowIdx, executionFlows, sigmaGraph]);

  // ---- Handlers ----

  // Unified grammar — DOUBLE CLICK = drill deeper (all views):
  //   hub       → toggle the radial blossom (expand eases the camera onto it)
  //   file/sat. → open the doc panel
  //   core      → no-op (Sigma's default camera zoom is allowed)
  // Returns true when an action ran so the canvas suppresses Sigma's default
  // double-click zoom; core returns void so the zoom-jump is kept.
  const handleSigmaDoubleClick = useCallback(
    (nodeId: string, nodeType: string): boolean | void => {
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
    [onNodeViewDocs, sigmaGraph, handleConstellationHubToggle],
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
    if (showShortcutHelp) {
      setShowShortcutHelp(false);
      return true;
    }
    if (showFlows) {
      setShowFlows(false);
      setActiveFlowIdx(null);
      return true;
    }
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
  }, [showShortcutHelp, selectedNodeId, communityPanelId, isConstellation, expandedHubs.length, collapseLast]);

  const handleToggleShortcutHelp = useCallback(() => {
    setShowShortcutHelp((s) => !s);
  }, []);

  // Global keyboard shortcuts (f/Escape/1-3//, cmd+k, ?)
  useGraphKeyboardShortcuts({
    sigmaRef,
    setSelectedNodeId,
    setEgoDepth,
    setSearchQuery,
    setCtxMenu,
    setCommunityPanelId,
    setColorMode,
    onEscape: handleEscapeCollapse,
    onToggleHelp: handleToggleShortcutHelp,
  });

  const handlePathFound = useCallback(
    (pathNodes: string[]) => {
      setHighlightedPath(new Set(pathNodes));
      setHighlightedEdges(traceToEdgeKeys(pathNodes));
      clearTimeout(focusTimerRef.current);
      focusTimerRef.current = setTimeout(() => {
        if (pathNodes.length > 0) {
          sigmaRef.current?.focusNode(pathNodes[0]!);
        }
      }, 800);
    },
    [],
  );

  const handlePathClear = useCallback(() => {
    setHighlightedPath(new Set());
    setHighlightedEdges(new Set());
  }, []);

  const handleFitView = useCallback(() => {
    sigmaRef.current?.fitView();
  }, []);

  const handleViewChange = useCallback((v: ViewMode) => {
    setViewModeState(v);
    onViewModeChange?.(v);
  }, [onViewModeChange]);

  // Everything a scope change has to clear, in one place. Scope can now arrive
  // from the host (the section-header switcher, URL-synced) as well as from the
  // toolbar's overlay buttons, so this reacts to the resolved value rather than
  // hanging off one of the two call sites — hooking it to the click handler
  // alone would leave a stale selection and a stale layout mode behind whenever
  // the host drove the change.
  const appliedViewModeRef = useRef(viewMode);
  useEffect(() => {
    if (appliedViewModeRef.current === viewMode) return;
    appliedViewModeRef.current = viewMode;
    // Constellation is fixed-radial; other scopes default back to FA2.
    setLayoutMode(viewMode === "architecture" ? "radial" : "force");
    setLayoutNotice(null);
    setHighlightedPath(new Set());
    setHighlightedEdges(new Set());
    setSelectedNodeId(null);
    // Leaving the constellation collapses any open blossoms.
    if (viewMode !== "architecture") collapseAllHubs();
  }, [viewMode, collapseAllHubs]);

  const handleLayoutModeChange = useCallback((mode: LayoutMode) => {
    // Refuse right at the click when ELK can't run: switching the mode anyway
    // would stop the force layout and leave an active-looking toggle doing
    // nothing (the canvas-side notice covers graphs that grow past the cap
    // after the mode is already active).
    if (mode === "hierarchical" && sigmaGraph && sigmaGraph.order > ELK_MAX_NODES) {
      setLayoutNotice(elkSkipReason(sigmaGraph.order));
      return;
    }
    setLayoutMode(mode);
    setLayoutNotice(null);
  }, [sigmaGraph]);

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

  // A rebuild can drop the selected node (module expanded into files) — clear
  // the selection then, or the reducer dims the whole canvas around a ghost.
  useEffect(() => {
    if (selectedNodeId && sigmaGraph && !sigmaGraph.hasNode(selectedNodeId)) {
      setSelectedNodeId(null);
    }
  }, [sigmaGraph, selectedNodeId]);

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
      setShowFlows(false);
      setActiveFlowIdx(null);
    }
  }, [selectedNodeId]);

  // Context menu actions
  const handleCtxViewDocs = useCallback(() => {
    if (ctxMenu) onNodeViewDocs?.(ctxMenu.nodeId);
    setCtxMenu(null);
  }, [ctxMenu, onNodeViewDocs, setCtxMenu]);

  const handleCtxExplore = useCallback(() => {
    if (ctxMenu) onNodeClick?.(ctxMenu.nodeId, ctxMenu.nodeType);
    setCtxMenu(null);
  }, [ctxMenu, onNodeClick, setCtxMenu]);

  const handleCtxPathFrom = useCallback(() => {
    if (ctxMenu) {
      setPathFrom(ctxMenu.nodeId);
      setShowPathFinder(true);
      setShowFlows(false);
      setActiveFlowIdx(null);
    }
    setCtxMenu(null);
  }, [ctxMenu, setCtxMenu]);

  const handleCtxPathTo = useCallback(() => {
    if (ctxMenu) {
      setPathTo(ctxMenu.nodeId);
      setShowPathFinder(true);
      setShowFlows(false);
      setActiveFlowIdx(null);
    }
    setCtxMenu(null);
  }, [ctxMenu, setCtxMenu]);

  if (isLoading || isAwaitingAsyncBuild || (isBuildingGraph && !sigmaGraph))
    return <Skeleton className="h-full w-full rounded-lg" />;

  // What the top-left status panel has to say, if anything. It is one bordered
  // panel now, so it must not render when every row inside it is empty — an
  // empty box is worse than the four separate chips it replaced.
  const showOverlayCounts =
    !!sigmaGraph && sigmaGraph.order > 0 && (isDeadView || isHotView);
  const hasCanvasStatus =
    (isEgoActive && !!selectedNodeId) || (showOverlayCounts && !!overlayStats);

  // The canvas is what the reader came for, so it sits on the page plane
  // rather than below it (rule 8). Dark mode used to paint
  // `--color-bg-inset`, which the July ramp move took a full step darker than
  // the `--color-bg-root` page around it, so the graph read as a hole cut in
  // the app. Light mode never painted anything and has always looked right;
  // this makes dark do what light already did. Same call the knowledge-graph
  // canvas made in `zoom/theme.ts`.
  return (
    <div className="relative w-full h-full" style={{ touchAction: "none", ...(graphTheme === "dark" ? { background: "var(--color-bg-root)" } : {}) }} aria-label="Dependency graph">
      {sigmaGraph && sigmaGraph.order > 0 ? (
        <SigmaCanvas
          ref={sigmaRef}
          graph={sigmaGraph}
          layoutMode={layoutMode}
          viewMode={viewMode}
          selectedNodeId={selectedNodeId}
          highlightedPath={highlightedPath}
          highlightedEdges={highlightedEdges}
          searchDimmedNodes={searchDimmedNodes}
          communityDimmedNodes={filterDimmedNodes}
          expandDimmedNodes={isConstellation ? expandDimmedNodes : null}
          colorMode={colorMode}
          activeSignals={activeSignals}
          graphTheme={graphTheme}
          fileNodes={fileGraphData?.nodes}
          fileEdges={fileGraphData?.links}
          onNodeClick={handleSigmaNodeClick}
          onNodeDoubleClick={handleSigmaDoubleClick}
          onNodeContextMenu={handleSigmaNodeContextMenu}
          onStageClick={() => setSelectedNodeId(null)}
          onLayoutSkipped={setLayoutNotice}
          hiddenNodes={isEgoActive ? hiddenNodes : undefined}
          visibleEdgeTypes={visibleEdgeTypes}
          depthRingRadii={isConstellation ? constellationRingRadii : null}
        />
      ) : !isLoading ? (
        <div className="flex items-center justify-center h-full">
          <EmptyState
            title={overlayEmptyState?.title ?? "No graph data"}
            description={
              overlayEmptyState?.description ??
              "This scope came back with nothing to draw. Try another scope, or re-index the repo if it was added recently."
            }
          />
        </div>
      ) : null}

      {/* Canvas status, top-left. One panel with hairline rows, matching the
          toolbar, the legend and the zoom controls — this corner could
          otherwise stack four independently-bordered, independently-shadowed
          chips (breadcrumb, expanded-modules, loading, tip, overlay counts)
          over the diagram at once. Rendered only when it has something to
          say, so an idle canvas carries no chrome here at all (rule 10). */}
      {hasCanvasStatus && (
      <div className={`absolute top-3 left-3 z-10 flex flex-col items-stretch overflow-hidden ${canvasPanelClass}`}>
      {isEgoActive && selectedNodeId ? (
        <div>
          <div className={canvasRowClass}>
            <span className="text-[10px] text-[var(--color-accent-primary)]">
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
      ) : null}

      {/* Overlay coverage: how many flagged files are actually in view. The
          totals come from the backend when it provides them; without totals
          we still report the in-view count so the overlay never reads as
          silently doing nothing. */}
      {showOverlayCounts && overlayStats && (
        <>
          {isDeadView && (
            <OverlayCountChip
              kind="dead"
              inView={overlayStats.deadInView}
              total={deadTotal}
            />
          )}
          {isHotView && (
            <OverlayCountChip
              kind="hot"
              inView={overlayStats.hotInView}
              total={hotTotal}
            />
          )}
        </>
      )}
      </div>
      )}

      {/* Layout-skipped notice: the hierarchical toggle must never look
          active while silently doing nothing. */}
      {layoutNotice && (
        <div
          role="status"
          aria-live="polite"
          className="absolute top-3 left-1/2 -translate-x-1/2 z-10 flex max-w-[min(28rem,calc(100vw-6rem))] items-center gap-2 rounded-lg border border-[var(--color-warning)]/40 bg-[var(--color-bg-elevated)]/95 backdrop-blur-sm px-3 py-1.5 shadow-sm"
        >
          <span className="text-[11px] text-[var(--color-text-primary)]">{layoutNotice}</span>
          <button
            onClick={() => setLayoutNotice(null)}
            aria-label="Dismiss layout notice"
            className="shrink-0 text-[var(--color-text-tertiary)] hover:text-[var(--color-text-primary)]"
          >
            <X className="w-3 h-3" />
          </button>
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
          onHideTestsChange={(v) => {
            setActiveSignals(prev => {
              const next = new Set(prev);
              v ? next.add("hideTests") : next.delete("hideTests");
              return next;
            });
          }}
          onFitView={handleFitView}
          showPathFinder={showPathFinder}
          pathFinderAvailable={Boolean(renderPathFinder)}
          onTogglePathFinder={() => {
            // Path finder and flows share the same overlay slot — opening
            // one always closes the other.
            setShowPathFinder((s) => !s);
            setShowFlows(false);
            setActiveFlowIdx(null);
          }}
          showFlows={showFlows}
          onToggleFlows={() => {
            setShowFlows((s) => !s);
            setActiveFlowIdx(null);
            setShowPathFinder(false);
          }}
          searchQuery={searchQuery}
          onSearchChange={setSearchQuery}
          searchMatchCount={searchResults.length}
          searchTotalCount={sigmaGraph?.order ?? 0}
          onSearchKeyDown={handleSearchKeyDown}
          layoutMode={layoutMode}
          onLayoutModeChange={handleLayoutModeChange}
          onToggleHelp={handleToggleShortcutHelp}
          hierarchicalDisabledReason={
            sigmaGraph && sigmaGraph.order > ELK_MAX_NODES
              ? elkSkipReason(sigmaGraph.order)
              : undefined
          }
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
              <span className="text-xs font-medium text-[var(--color-text-primary)]">
                Execution Flows
              </span>
              <div className="flex items-center gap-1.5">
                <span className="text-[10px] text-[var(--color-text-tertiary)]">
                  {executionFlows.flows.length} entry points
                </span>
                {/* Same close affordance as the Path Finder panel above. */}
                <button
                  onClick={() => {
                    setShowFlows(false);
                    setActiveFlowIdx(null);
                  }}
                  aria-label="Close"
                  title="Close"
                  className="text-[var(--color-text-tertiary)] hover:text-[var(--color-text-primary)]"
                >
                  <X className="w-3 h-3" />
                </button>
              </div>
            </div>
            <div className="space-y-1 max-h-60 overflow-y-auto">
              {executionFlows.flows.map((flow, idx) => (
                <button
                  key={flow.entry_point}
                  onClick={() => setActiveFlowIdx(activeFlowIdx === idx ? null : idx)}
                  className={`w-full text-left px-2 py-1.5 rounded-md text-xs transition-colors ${
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
            {activeFlowMissingCount > 0 && (
              <p className="mt-2 text-[10px] leading-snug text-[var(--color-warning)]">
                This flow includes {activeFlowMissingCount} node
                {activeFlowMissingCount === 1 ? "" : "s"} not in the loaded
                view — load more nodes to see the full trace.
              </p>
            )}
          </div>
        </div>
      )}

      {/* Legend — on phones the inspection bottom sheet covers this corner,
          so yield to it instead of stacking underneath. */}
      <div className={`absolute bottom-3 left-3 z-10 ${selectedNodeId ? "hidden sm:block" : ""}`}>
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

      {/* Keyboard shortcut help (toggled with ?) */}
      {showShortcutHelp && (
        <GraphShortcutHelp onClose={() => setShowShortcutHelp(false)} />
      )}

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
            onViewSymbols={
              fileNd && onNodeViewSymbols
                ? () => { onNodeViewSymbols(selectedNodeId); }
                : undefined
            }
            filePageHref={fileNd ? fileHrefFor?.(selectedNodeId) : undefined}
            onFindPath={handleInspectFindPath}
            isModuleExpanded={false}
            egoDepth={egoDepth}
            onEgoDepthChange={setEgoDepth}
            egoVisibleCount={egoVisibleCount}
          />
        );
      })()}
    </div>
  );
}

/**
 * Chrome for the top-left status panel. Shares the toolbar / legend / zoom
 * treatment so every corner of the canvas reads as one system: one border, one
 * shadow, one blur, and hairline dividers instead of gaps between boxes.
 */
const canvasPanelClass =
  "rounded-lg border border-[var(--color-border-default)] bg-[var(--color-bg-elevated)]/85 shadow-sm backdrop-blur-sm";

/** One row inside that panel. `first:border-t-0` keeps the top edge clean. */
const canvasRowClass =
  "flex items-center gap-2 border-t border-[var(--color-border-default)] px-2.5 py-1.5 first:border-t-0";

/** Small status chip captioning a dead/hot view: "12 of 37 dead files in
 *  view" when the backend supplies repo-wide totals, or just the in-view
 *  count when it doesn't. */
function OverlayCountChip({
  kind,
  inView,
  total,
}: {
  kind: "dead" | "hot";
  inView: number;
  total: number | null;
}) {
  const noun = kind === "dead" ? "dead files" : "hot files";
  let text: string;
  if (total != null && inView < total) {
    text = `${inView} of ${total} ${noun} in view — the rest are outside the loaded node set`;
  } else if (total != null) {
    text = `Showing all ${total} ${noun}`;
  } else {
    text = `${inView} ${noun} in view`;
  }
  return (
    <div role="status" className={canvasRowClass}>
      <span className="text-[10px] text-[var(--color-text-secondary)]">{text}</span>
    </div>
  );
}
