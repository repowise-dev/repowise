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

import { usePrefersReducedMotion } from "../hooks/use-prefers-reduced-motion";
import { GraphScopeBreadcrumb } from "./graph-scope-breadcrumb";
import { traceToEdgeKeys, traceToFileTrace } from "./graph-flow-helpers";
import { useGraphContextMenu } from "./use-graph-context-menu";
import { useGraphSearch } from "./use-graph-search";
import { useCommunityFilter } from "./use-community-filter";
import { useModuleFilter, filterGraphToModule } from "./use-module-filter";
import { useGraphKeyboardShortcuts } from "./use-graph-keyboard-shortcuts";
import { GraphToolbar, type ColorMode, type ViewMode, type LayoutMode, type GraphTheme } from "./graph-toolbar";
import { GraphLegend } from "./graph-legend";
import { GraphNodeFilter } from "./graph-toolbar";
import { GraphCanvasShell } from "./graph-canvas-shell";
import { GraphContextMenu } from "./graph-context-menu";
import { GraphInspectionPanel } from "./graph-inspection-panel";
import { GraphFlowPanel } from "./graph-flow-panel";
import { GraphShortcutHelp } from "./graph-shortcut-help";
import { GraphPopulationControl } from "./graph-population-control";
import { GraphUnclusteredPanel } from "./graph-unclustered-panel";
import type {
  GraphExport,
  ExecutionFlows,
  CommunitySummaryItem,
  ArchitectureGraph,
  CommunitySlice,
  GraphPopulation,
} from "@repowise-dev/types/graph";
import { SigmaCanvas, type SigmaCanvasHandle } from "./sigma/sigma-canvas";
import {
  fileGraphToGraphology,
  fileGraphToGraphologyAsync,
} from "./sigma/graphology-adapter";
import {
  architectureToGraphology,
  hubNodeId,
  UNCLUSTERED_COMMUNITY_ID,
} from "./sigma/constellation-adapter";
import { computeRadialLayout } from "./sigma/radial-layout";
import { ELK_MAX_NODES, elkSkipReason } from "./sigma/use-elk-sigma-layout";
import type { SigmaNodeAttributes, SigmaEdgeAttributes } from "./sigma/types";
import type GraphologyGraph from "graphology";
import { useEgoFilter } from "./sigma/use-ego-filter";

export interface GraphFlowProps {
  fullGraph: GraphExport | undefined;
  isLoadingFullGraph: boolean;
  /** @deprecated Unread. The "architecture" scope draws the constellation from
   *  {@link constellationGraph}, not a file-graph export. Kept optional so an
   *  out-of-tree host compiles while it drops them. */
  architectureGraph?: GraphExport | undefined;
  /** @deprecated Unread. See {@link architectureGraph}. */
  isLoadingArchitectureGraph?: boolean | undefined;
  /** Community super-graph for the constellation (radial Knowledge Graph) scope. */
  constellationGraph?: ArchitectureGraph | undefined;
  isLoadingConstellationGraph?: boolean;
  /** @deprecated Unread. Hubs no longer blossom satellites in place: a
   *  double-click *enters* the community and draws its own file graph. See
   *  {@link communitySlice}. Kept optional so a host compiles while it drops it. */
  constellationSlices?: Map<number, CommunitySlice> | undefined;
  /** @deprecated Unread. See {@link onActiveCommunityChange}. */
  onExpandedHubsChange?: (expanded: number[]) => void;
  /** The community currently being drilled into, or null for the whole scope.
   *  Controlled by the host, which URL-syncs it as `?community=`. */
  activeCommunity?: number | null | undefined;
  /** Fired when the reader enters or leaves a community (hub double-click, the
   *  panel's Enter action, the breadcrumb, Escape). The host writes `?community=`
   *  and, on entry, switches the scope to files. */
  onActiveCommunityChange?: ((communityId: number | null) => void) | undefined;
  /** The entered community's own sub-graph: its members plus one-hop boundary
   *  stubs. Fetched by the host in response to {@link onActiveCommunityChange}. */
  communitySlice?: CommunitySlice | undefined;
  isLoadingCommunitySlice?: boolean | undefined;
  /** Which non-production files the community views count. Controlled by the
   *  host, which fetches with it. The control renders only when both are
   *  supplied. */
  population?: GraphPopulation | undefined;
  onPopulationChange?: ((next: GraphPopulation) => void) | undefined;
  /** Repo name for the constellation core label. */
  repoName?: string;
  deadCodeGraph: GraphExport | undefined;
  isLoadingDeadCodeGraph: boolean;
  hotFilesGraph: GraphExport | undefined;
  isLoadingHotFilesGraph: boolean;
  communities?: CommunitySummaryItem[];
  executionFlows?: ExecutionFlows;
  /** Fired when the flows panel opens or closes, so a host can defer fetching
   *  {@link executionFlows} until someone asks. Optional; ignoring it keeps the
   *  eager behaviour. */
  onFlowsVisibilityChange?: ((visible: boolean) => void) | undefined;
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
  /** Per-file destinations for the inspector's outbound actions: where this
   *  file's health, git history and decisions live. Optional; each action is
   *  hidden when the host does not supply it. */
  fileHealthHrefFor?: ((nodeId: string) => string) | undefined;
  fileHistoryHrefFor?: ((nodeId: string) => string) | undefined;
  fileDecisionsHrefFor?: ((nodeId: string) => string) | undefined;
  /** The repo's dead-code findings. Offered only on a node flagged dead. */
  deadCodeHref?: string | undefined;
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
    /** Draw this community's own scoped file graph (the drill-down). */
    onEnterCommunity: () => void;
    /** Open a neighbouring community's panel, without leaving the canvas. */
    onNeighborSelect: (communityId: number) => void;
  }) => ReactNode;
  /** Fired when the community detail panel transitions to open. */
  onCommunityPanelOpen?: (communityId: number) => void;
  /** Fired when canvas selection changes. A host owning `rail` content tied to
   *  a node (a doc panel) uses this to drop it once a different node is
   *  selected, instead of masking the inspector indefinitely. */
  onSelectedNodeChange?: ((nodeId: string | null) => void) | undefined;
  /** One-line explanation of the current scope, shown in the header row. */
  description?: string;
  /** Host controls placed left of the toolbar (scope switcher, narrowing
   *  control). */
  headerActions?: ReactNode;
  /** Full-width notice above the canvas (e.g. the truncation banner). */
  banner?: ReactNode;
  /** Host-owned rail content, e.g. a documentation panel. Takes the rail over
   *  the community and inspection panels; see the precedence in the render. */
  rail?: ReactNode;
}

export function GraphFlow(props: GraphFlowProps) {
  const {
    fullGraph,
    isLoadingFullGraph,
    constellationGraph,
    isLoadingConstellationGraph,
    activeCommunity: controlledActiveCommunity,
    population,
    onPopulationChange,
    onActiveCommunityChange,
    communitySlice,
    isLoadingCommunitySlice,
    repoName,
    deadCodeGraph,
    isLoadingDeadCodeGraph,
    hotFilesGraph,
    isLoadingHotFilesGraph,
    communities,
    executionFlows,
    onFlowsVisibilityChange,
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
    fileHealthHrefFor,
    fileHistoryHrefFor,
    fileDecisionsHrefFor,
    deadCodeHref,
    renderPathFinder,
    renderCommunityPanel,
    onCommunityPanelOpen,
    onSelectedNodeChange,
    description,
    headerActions,
    banner,
    rail,
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

  // How much of a found path this view cannot draw. See `handlePathFound`.
  const [pathNotice, setPathNotice] = useState<string | null>(null);

  // Both structural kinds, because the description under every file-scope
  // reading promises "how they depend on each other". The old default was
  // `["import", "crossCommunity"]`, and `"import"` was an unreachable
  // classification — `classifyEdge` returned it only for an edge whose
  // endpoint was missing from the graph, and those are dropped before they are
  // drawn. So the default meant "cross-community only", and inside a community
  // that hid the entire internal structure and drew only the exits.
  //
  // This raises the whole-repo default too, deliberately: measured on this
  // repo's own 1,500-node graph it goes from 1,052 to 5,027 drawn edges, an
  // average degree of 3.35, well short of a hairball and the difference
  // between drawing 21% of the structure and all of it.
  const [visibleEdgeTypes, setVisibleEdgeTypes] = useState<Set<string>>(
    () => new Set(["crossCommunity", "internal"]),
  );

  // Signal overlays (replaces separate view modes for dead/hot/arch).
  // Derived from the host-provided initial view mode — no URL reads here.
  const activeSignals = useMemo<Set<Signal>>(() => {
    if (initialViewMode === "dead") return new Set<Signal>(["dead"]);
    if (initialViewMode === "hotfiles") return new Set<Signal>(["hot"]);
    if (initialViewMode === "unified") return new Set<Signal>(["dead", "hot"]);
    return new Set<Signal>();
  }, [initialViewMode]);
  const hideTests = activeSignals.has("hideTests");

  // Drill-down: the community whose own file graph is drawn.
  // (reconciliation effect lives below, once the panel state exists) The host owns it
  // (`?community=`); the local fallback keeps an uncontrolled host working.
  const [activeCommunityState, setActiveCommunityState] = useState<number | null>(null);
  const activeCommunity =
    controlledActiveCommunity !== undefined
      ? controlledActiveCommunity
      : activeCommunityState;
  const setActiveCommunity = useCallback(
    (next: number | null) => {
      if (onActiveCommunityChange) onActiveCommunityChange(next);
      else setActiveCommunityState(next);
    },
    [onActiveCommunityChange],
  );

  const prefersReducedMotion = usePrefersReducedMotion();

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
  // Stable, so the memoised panel does not re-render with the canvas.
  const handleFlowSelect = useCallback(
    (idx: number) => setActiveFlowIdx((cur) => (cur === idx ? null : idx)),
    [],
  );
  const handleFlowsClose = useCallback(() => {
    setShowFlows(false);
    setActiveFlowIdx(null);
  }, []);

  // Reported from one place, not from each of the six setters that close it.
  useEffect(() => {
    onFlowsVisibilityChange?.(showFlows);
  }, [showFlows, onFlowsVisibilityChange]);

  useEffect(() => {
    onSelectedNodeChange?.(selectedNodeId);
  }, [selectedNodeId, onSelectedNodeChange]);

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
      if (cid === UNCLUSTERED_COMMUNITY_ID) {
        // Not a community: the host is not told, so it never fetches one.
        setCommunityPanelId(cid);
        return;
      }
      const nodeId = hubNodeId(cid);
      setSelectedNodeId(nodeId);
      sigmaRef.current?.focusNode(nodeId, HUB_FOCUS_RATIO);
      openCommunityPanel(cid);
    },
    [openCommunityPanel],
  );

  // Scope changes arrive from the toolbar, the host's switcher, and the
  // drill-down below, so it lives above all three.
  const handleViewChange = useCallback((v: ViewMode) => {
    setViewModeState(v);
    onViewModeChange?.(v);
  }, [onViewModeChange]);

  // Hub double-click ENTERS the community: the canvas swaps to that community's
  // own file graph, scoped, with its one-hop neighbours as the edge of the
  // world. The hub used to blossom satellites in place, which showed you the
  // files without ever letting you work on them.
  //
  // Camera continuity is what makes this read as one movement. The hub's
  // position is handed to the renderer as the point the *next* graph opens
  // from, so the scoped layout eases outward from where the disc was rather
  // than cutting to a fresh frame. `prefers-reduced-motion` skips the travel.
  const enterCommunity = useCallback(
    (cid: number) => {
      const nodeId = hubNodeId(cid);
      if (!prefersReducedMotion) {
        // Tighter than HUB_FOCUS_RATIO: the movement opens *out* of the hub.
        sigmaRef.current?.setEntryCamera(sigmaRef.current.nodeCamera(nodeId, 0.08));
      }
      setSelectedNodeId(null);
      setPathNotice(null);
      setActiveCommunity(cid);
      openCommunityPanel(cid);
      // Files scope, since a community's members are files. A controlled host
      // reflects this back through `viewMode`.
      handleViewChange("full");
    },
    [prefersReducedMotion, setActiveCommunity, openCommunityPanel, handleViewChange],
  );

  /** Back out of a community to the constellation it came from. */
  const leaveCommunity = useCallback(() => {
    // Drop any entry camera that was armed but never consumed (an entry whose
    // slice failed to arrive), so it cannot seed an unrelated later swap.
    sigmaRef.current?.setEntryCamera(null);
    setSelectedNodeId(null);
    setCommunityPanelId(null);
    setActiveCommunity(null);
    handleViewChange("architecture");
  }, [setActiveCommunity, handleViewChange]);

  // The community can also change from outside this component: the narrowing
  // control writes `?community=` directly, the scope switcher clears it, and a
  // shared link arrives with one already set. Those routes never run
  // `enterCommunity`/`leaveCommunity`, so a panel describing the community you
  // just left would stay in the rail, and an armed entry camera would survive
  // to seed an unrelated later graph swap.
  const appliedCommunityRef = useRef(activeCommunity);
  useEffect(() => {
    const previous = appliedCommunityRef.current;
    if (previous === activeCommunity) return;
    appliedCommunityRef.current = activeCommunity;
    // Only the panel for the community being left; `enterCommunity` has already
    // pointed it at the new one by the time this runs.
    setCommunityPanelId((open) => (open !== null && open === previous ? null : open));
    if (activeCommunity === null) sigmaRef.current?.setEntryCamera(null);
  }, [activeCommunity]);

  // ---- Derived state ----
  const isUnified = viewMode === "unified";
  // The "architecture" scope renders the radial community constellation.
  const isConstellation = viewMode === "architecture";

  // Constellation graph: one hub per community + repo-core, radial positions.
  const constellationSigmaGraph = useMemo(() => {
    if (!isConstellation || !constellationGraph) return null;
    return architectureToGraphology(
      constellationGraph,
      repoName ? { repoName } : {},
    );
  }, [isConstellation, constellationGraph, repoName]);

  // Ring radii for the depth-ring underlay (graph coordinates).
  const constellationRingRadii = useMemo(() => {
    // Not gated on the scope: the held frame during a drill-down is still the
    // constellation, and rings that vanish out from under it read as a glitch.
    if (!constellationGraph) return null;
    return computeRadialLayout(
      constellationGraph.nodes.map((n) => ({
        community_id: n.community_id,
        member_count: n.member_count,
        avg_pagerank: n.avg_pagerank,
      })),
    ).ringRadii;
  }, [constellationGraph]);

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

  // Whether a community is actually being drawn: `?community=` only means
  // anything on a file-level scope, so a stale one carried into the
  // constellation is ignored rather than half-applied.
  const isInsideCommunity = !isConstellation && activeCommunity !== null;

  // "This community talks to that one; take me there." From the constellation
  // that means selecting the neighbour hub and framing it; from inside a
  // community it means entering the neighbour, because that is the altitude
  // the reader is already at.
  const handleNeighborSelect = useCallback(
    (cid: number) => {
      if (isInsideCommunity) enterCommunity(cid);
      else handleConstellationHubClick(cid);
    },
    [isInsideCommunity, enterCommunity, handleConstellationHubClick],
  );

  // The file-level payload *before* narrowing. The module group list is derived
  // from this, so choosing a module never empties the menu it came from.
  const scopeGraphData = useMemo(() => {
    switch (viewMode) {
      case "full":
      case "unified":
        return fullGraph ? { nodes: fullGraph.nodes, links: fullGraph.links } : undefined;
      // "architecture" renders the radial constellation, not a file graph.
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

  // Boundary stubs of the entered community: one-hop neighbours the slice
  // carries as context. Drawn smaller and desaturated, so the scoped graph has
  // an edge instead of trailing off into nothing.
  const sliceBoundaryIds = useMemo(() => {
    if (!isInsideCommunity || !communitySlice) return undefined;
    const ids = new Set<string>();
    for (const n of communitySlice.nodes) if (n.is_boundary) ids.add(n.node_id);
    return ids.size > 0 ? ids : undefined;
  }, [isInsideCommunity, communitySlice]);

  // What actually gets built and drawn. A community replaces the payload
  // outright (it is its own graph, not a view of the capped one); a module
  // narrows it. They are one axis and never both apply.
  const fileGraphData = useMemo(() => {
    if (isInsideCommunity) {
      return communitySlice
        ? { nodes: communitySlice.nodes, links: communitySlice.links }
        : undefined;
    }
    if (!scopeGraphData) return undefined;
    return filterGraphToModule(scopeGraphData, controlledActiveModule ?? null);
  }, [isInsideCommunity, communitySlice, scopeGraphData, controlledActiveModule]);

  // Loading state
  const isLoading =
    isInsideCommunity ? !!isLoadingCommunitySlice :
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
      { signals, ...(sliceBoundaryIds ? { boundaryNodeIds: sliceBoundaryIds } : {}) },
    );
  }, [fileGraphData, hasHotSignal, hasDeadSignal, isUnified, hotNodeIds, deadNodeIds, sliceBoundaryIds]);

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
      { signals, ...(sliceBoundaryIds ? { boundaryNodeIds: sliceBoundaryIds } : {}) },
    ).then((graph) => {
      if (cancelled) return;
      setAsyncSigmaGraph(graph);
      setIsBuildingGraph(false);
    });

    return () => {
      cancelled = true;
    };
  }, [needsAsyncBuild, fileGraphData, hasHotSignal, hasDeadSignal, isUnified, hotNodeIds, deadNodeIds, sliceBoundaryIds]);

  const sigmaGraph = isConstellation
    ? constellationSigmaGraph
    : (syncSigmaGraph ?? asyncSigmaGraph);

  // Entering a community swaps the payload, and the slice is a round trip.
  // Blanking to a skeleton in between would unmount the renderer, and with it
  // the camera that makes the drill-down read as one movement rather than two
  // pages. So the last frame is held until the slice lands, and only for that
  // transition — every other empty graph still reports itself honestly.
  const heldGraphRef = useRef<GraphologyGraph<
    SigmaNodeAttributes,
    SigmaEdgeAttributes
  > | null>(null);
  if (sigmaGraph) heldGraphRef.current = sigmaGraph;
  // Gated on the fetch actually being in flight. Holding on `!communitySlice`
  // alone meant a *failed* slice pinned the previous frame forever: SWR leaves
  // `data` undefined and `isLoading` false between retries, so the constellation
  // stayed drawn under a breadcrumb and a description both asserting a scoped
  // file graph, with no error and no empty state anywhere.
  const isEnteringCommunity =
    isInsideCommunity && !communitySlice && !!isLoadingCommunitySlice;
  const displayGraph =
    sigmaGraph ?? (isEnteringCommunity ? heldGraphRef.current : null);

  const { hiddenNodes, isActive: isEgoActive, visibleCount: egoVisibleCount } = useEgoFilter({
    graph: displayGraph,
    selectedNodeId,
    depth: egoDepth,
  });

  // Node data maps (sorted metrics moved into GraphInspectionPanel)
  const sigmaNodeMaps = useMemo(() => {
    if (!displayGraph) return null;

    const fileMap = new Map<string, FileNodeData>();
    const modMap = new Map<string, ModuleNodeData>();

    displayGraph.forEachNode((nodeId, attrs) => {
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
        if (attrs.hasDecision) fileData.hasDecision = true;
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
  }, [displayGraph]);

  const effectiveNodeDataMap = sigmaNodeMaps?.fileMap ?? new Map<string, FileNodeData>();
  const effectiveModuleDataMap = sigmaNodeMaps?.modMap ?? new Map<string, ModuleNodeData>();

  // How many flagged nodes actually made it into the rendered graph — paired
  // with the repo-wide totals to caption the dead/hot views honestly.
  const overlayStats = useMemo(() => {
    if (!displayGraph) return null;
    let deadInView = 0;
    let hotInView = 0;
    displayGraph.forEachNode((_, attrs) => {
      if (attrs.isDead) deadInView++;
      if (attrs.isHotspot) hotInView++;
    });
    return { deadInView, hotInView };
  }, [displayGraph]);

  // A community slice is its own payload and was never filtered to dead or hot
  // files, so the signal captions must not describe it. Reachable from
  // `?view=files&signal=dead` plus a community pick.
  const isDeadView =
    !isInsideCommunity && (viewMode === "dead" || viewMode === "unified");
  const isHotView =
    !isInsideCommunity && (viewMode === "hotfiles" || viewMode === "unified");

  // Trace nodes of the selected execution flow that fell outside the loaded
  // node set — highlighting/focus silently no-op for them, so tell the user.
  const activeFlowMissingCount = useMemo(() => {
    if (activeFlowIdx === null || !executionFlows || !displayGraph) return 0;
    const flow = executionFlows.flows[activeFlowIdx];
    if (!flow) return 0;
    return traceToFileTrace(flow.trace).filter((id) => !displayGraph.hasNode(id)).length;
  }, [activeFlowIdx, executionFlows, displayGraph]);

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
    useGraphSearch({ sigmaGraph: displayGraph, hideTests, panToNode, setSelectedNodeId });

  // Community filter (active communities + dimming + legend toggles)
  const {
    activeCommunities,
    communityDimmedNodes,
    drawnCommunityIds,
    handleCommunityToggle,
    handleToggleAllCommunities,
  } = useCommunityFilter(displayGraph);

  // Module groups, offered to the host's narrowing control. Derived from the
  // scope's *unfiltered* payload: the filter now removes nodes rather than
  // dimming them, so deriving the menu from the drawn graph would leave one
  // option in it the moment you used it.
  const { moduleGroups } = useModuleFilter(
    isInsideCommunity ? undefined : scopeGraphData?.nodes,
    controlledActiveModule ?? null,
  );
  useEffect(() => {
    onModuleGroupsChange?.(moduleGroups);
  }, [moduleGroups, onModuleGroupsChange]);

  // Flow index whose trace head has already been focused, so the deferred
  // re-focus below fires at most once per selection and never re-steers the
  // camera on later graph changes while the same flow stays active.
  const flowFocusedRef = useRef<number | null>(null);
  // Live graph handle for the focus timer (the effect below deliberately
  // keeps displayGraph out of its deps).
  const drawnGraphRef = useRef(displayGraph);
  drawnGraphRef.current = displayGraph;

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
      if (drawnGraphRef.current?.hasNode(firstNode)) {
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
    if (!firstNode || !displayGraph?.hasNode(firstNode)) return;
    flowFocusedRef.current = activeFlowIdx;
    sigmaRef.current?.focusNode(firstNode);
  }, [activeFlowIdx, executionFlows, displayGraph]);

  // ---- Handlers ----

  // Unified grammar — DOUBLE CLICK = drill deeper (all views):
  //   hub       → toggle the radial blossom (expand eases the camera onto it)
  //   file/sat. → open the doc panel
  //   core      → no-op (Sigma's default camera zoom is allowed)
  // Returns true when an action ran so the canvas suppresses Sigma's default
  // double-click zoom; core returns void so the zoom-jump is kept.
  const handleSigmaDoubleClick = useCallback(
    (nodeId: string, nodeType: string): boolean | void => {
      if (nodeType === "hub" && displayGraph?.hasNode(nodeId)) {
        const cid = displayGraph.getNodeAttribute(nodeId, "communityId");
        if (typeof cid === "number" && cid >= 0) {
          enterCommunity(cid);
          return true;
        }
        return;
      }
      if (nodeType === "core") return;
      onNodeViewDocs?.(nodeId);
      return true;
    },
    [onNodeViewDocs, displayGraph, enterCommunity],
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
      if (nodeType === "hub" && displayGraph?.hasNode(nodeId)) {
        const cid = displayGraph.getNodeAttribute(nodeId, "communityId");
        // The "not grouped" disc is a list, not a community: it opens its panel
        // without being selected, since selection dims everything it does not
        // link to, which is everything.
        if (cid === UNCLUSTERED_COMMUNITY_ID) {
          setCommunityPanelId(cid);
          return;
        }
        if (typeof cid === "number" && cid >= 0) {
          setSelectedNodeId(nodeId);
          sigmaRef.current?.focusNode(nodeId, HUB_FOCUS_RATIO);
          openCommunityPanel(cid);
          return;
        }
      }
      setSelectedNodeId(nodeId);
      // The community panel describes a hub; selecting a file replaces it in
      // the rail rather than outranking the inspector forever.
      setCommunityPanelId(null);
    },
    [selectedNodeId, displayGraph, openCommunityPanel],
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
    if (isInsideCommunity) {
      leaveCommunity();
      return true;
    }
    return false;
  }, [showShortcutHelp, selectedNodeId, communityPanelId, isInsideCommunity, leaveCommunity]);

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
      // The path endpoint is repo-wide while the canvas may be a module filter
      // or one community, so a path can legitimately run through files this
      // view does not draw. `focusNode` no-ops on those, which read as the
      // control doing nothing. Focus the first file that IS here, and say how
      // many are not — the same courtesy the flows panel already extends.
      const graph = drawnGraphRef.current;
      const drawn = graph ? pathNodes.filter((id) => graph.hasNode(id)) : pathNodes;
      const missing = pathNodes.length - drawn.length;
      setPathNotice(
        missing > 0
          ? `${missing} of ${pathNodes.length} files on this path are outside the current view.`
          : null,
      );
      clearTimeout(focusTimerRef.current);
      focusTimerRef.current = setTimeout(() => {
        if (drawn.length > 0) {
          sigmaRef.current?.focusNode(drawn[0]!);
        }
      }, 800);
    },
    [],
  );

  const handlePathClear = useCallback(() => {
    setHighlightedPath(new Set());
    setHighlightedEdges(new Set());
    setPathNotice(null);
  }, []);

  const handleFitView = useCallback(() => {
    sigmaRef.current?.fitView();
  }, []);

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
    // Measured against the graph being replaced, so it retires with it.
    setPathNotice(null);
    setSelectedNodeId(null);
    // Back to the constellation by any route (the switcher, a legacy link):
    // the drill-down is a file-scope state and does not survive leaving it.
    if (viewMode === "architecture") setActiveCommunity(null);
  }, [viewMode, setActiveCommunity]);

  const handleLayoutModeChange = useCallback((mode: LayoutMode) => {
    // Refuse right at the click when ELK can't run: switching the mode anyway
    // would stop the force layout and leave an active-looking toggle doing
    // nothing (the canvas-side notice covers graphs that grow past the cap
    // after the mode is already active).
    if (mode === "hierarchical" && displayGraph && displayGraph.order > ELK_MAX_NODES) {
      setLayoutNotice(elkSkipReason(displayGraph.order));
      return;
    }
    setLayoutMode(mode);
    setLayoutNotice(null);
  }, [displayGraph]);

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
    if (selectedNodeId && displayGraph && !displayGraph.hasNode(selectedNodeId)) {
      setSelectedNodeId(null);
    }
  }, [displayGraph, selectedNodeId]);

  const initialNodeApplied = useRef(false);
  useEffect(() => {
    if (initialNodeApplied.current || !initialSelectedNode || !displayGraph) return;
    if (displayGraph.hasNode(initialSelectedNode)) {
      initialNodeApplied.current = true;
      setSelectedNodeId(initialSelectedNode);
      setTimeout(() => panToNode(initialSelectedNode), 300);
    }
  }, [initialSelectedNode, displayGraph, panToNode]);

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

  // Both of these are read far below, next to the key they feed. They are
  // declared here because the skeleton branch that follows returns early, and a
  // hook after it does not run on the loading pass — which changes the hook
  // count between renders and takes the whole canvas down.

  // What the key reports. `displayGraph.size` counts every edge in the graph
  // including the kinds the edge filter is hiding, which is how a slice showing
  // only its exits could still claim 553 edges. Reconciles the edge filter
  // only: the ego filter hides nodes at the canvas rather than in the graph,
  // and reports its own count in the status row beside this.
  const visibleEdgeCount = useMemo(() => {
    if (!displayGraph) return 0;
    let n = 0;
    displayGraph.forEachEdge((_, attrs) => {
      if (visibleEdgeTypes.has(attrs.edgeKind)) n++;
    });
    return n;
  }, [displayGraph, visibleEdgeTypes]);

  // Members vs the one-hop stubs around them, from the same payload the banner
  // counts, so the two figures on screen cannot disagree.
  const sliceCounts = useMemo(() => {
    if (!communitySlice) return undefined;
    const members = communitySlice.nodes.filter((n) => !n.is_boundary).length;
    return { members, boundary: communitySlice.nodes.length - members };
  }, [communitySlice]);

  // No hooks below this line.
  if (
    (isLoading || isAwaitingAsyncBuild || isBuildingGraph) &&
    !displayGraph
  )
    return <Skeleton className="h-full w-full rounded-lg" />;

  const showOverlayCounts =
    !!displayGraph && displayGraph.order > 0 && (isDeadView || isHotView);
  const hasCanvasStatus =
    (isEgoActive && !!selectedNodeId) || (showOverlayCounts && !!overlayStats);

  const inspectedFile = selectedNodeId
    ? effectiveNodeDataMap.get(selectedNodeId)
    : undefined;
  const inspectedNode = selectedNodeId
    ? (inspectedFile ?? effectiveModuleDataMap.get(selectedNodeId))
    : undefined;

  // One rail, most-explicit intent first. Path finder and flows are opened by
  // hand, docs are asked for by name, a community panel follows a hub click,
  // and the inspector is merely a selection. Three panels used to stack on the
  // same edge with a host callback arbitrating between them.
  const railContent = showPathFinder && renderPathFinder
    ? renderPathFinder({
        initialFrom: pathFrom,
        initialTo: pathTo,
        onPathFound: handlePathFound,
        onClear: handlePathClear,
        onClose: () => {
          setShowPathFinder(false);
          setPathNotice(null);
        },
      })
    : showFlows
      ? executionFlows && executionFlows.flows.length > 0
        ? (
          <GraphFlowPanel
            flows={executionFlows}
            activeFlowIdx={activeFlowIdx}
            onSelect={handleFlowSelect}
            onClose={handleFlowsClose}
            missingCount={activeFlowMissingCount}
          />
        )
        : (
          // The trace fetch is deferred until this panel opens, so the first
          // open has nothing yet. Saying so beats an empty rail.
          <div className="p-4 text-xs text-[var(--color-text-secondary)]">
            {executionFlows ? "No execution flows for this scope." : "Loading execution flows…"}
          </div>
        )
      : rail
        ? rail
        : communityPanelId === UNCLUSTERED_COMMUNITY_ID && constellationGraph?.unclustered
          ? (
            <GraphUnclusteredPanel
              unclustered={constellationGraph.unclustered}
              onClose={() => setCommunityPanelId(null)}
              fileHrefFor={fileHrefFor}
            />
          )
        : communityPanelId !== null &&
            communityPanelId !== UNCLUSTERED_COMMUNITY_ID &&
            renderCommunityPanel
          ? renderCommunityPanel({
              communityId: communityPanelId,
              onClose: () => setCommunityPanelId(null),
              onEnterCommunity: () => enterCommunity(communityPanelId),
              onNeighborSelect: handleNeighborSelect,
            })
          : selectedNodeId && inspectedNode
            ? (
              <GraphInspectionPanel
                nodeId={selectedNodeId}
                data={inspectedNode}
                graph={displayGraph}
                allNodes={effectiveNodeDataMap}
                communityLabel={
                  inspectedFile
                    ? communityLabels?.get(inspectedFile.communityId)
                    : undefined
                }
                onClose={() => { setSelectedNodeId(null); }}
                onNavigateToNode={handleInspectNavigate}
                onViewDocs={() => { onNodeViewDocs?.(selectedNodeId); }}
                onViewSymbols={
                  inspectedFile && onNodeViewSymbols
                    ? () => { onNodeViewSymbols(selectedNodeId); }
                    : undefined
                }
                filePageHref={inspectedFile ? fileHrefFor?.(selectedNodeId) : undefined}
                healthHref={inspectedFile ? fileHealthHrefFor?.(selectedNodeId) : undefined}
                historyHref={inspectedFile ? fileHistoryHrefFor?.(selectedNodeId) : undefined}
                decisionsHref={
                  inspectedFile ? fileDecisionsHrefFor?.(selectedNodeId) : undefined
                }
                deadCodeHref={deadCodeHref}
                onFindPath={handleInspectFindPath}
                isModuleExpanded={false}
                egoDepth={egoDepth}
                onEgoDepthChange={setEgoDepth}
                egoVisibleCount={egoVisibleCount}
              />
            )
            : undefined;

  // Inside a community the host's own description describes the wrong thing:
  // it was written for "every file in the repo". The scoped one is stated here
  // because only this component knows the drill-down happened.
  // The slice is capped server-side (SLICE_MEMBER_CAP), and the boundary stubs
  // are drawn but are not members. Both make the drawn node count disagree with
  // "the files in X", and the repo-wide truncation banner is deliberately off
  // in this scope, so the honest sentence has to come from here.
  const sliceNotice = (() => {
    if (!isInsideCommunity || !communitySlice) return null;
    const drawn = communitySlice.nodes.filter((n) => !n.is_boundary).length;
    const stubs = communitySlice.nodes.length - drawn;
    const parts: string[] = [];
    if (communitySlice.truncated && communitySlice.member_count > drawn) {
      parts.push(
        `Showing the ${drawn} most connected of ${communitySlice.member_count} files in this group`,
      );
    } else {
      parts.push(`Showing all ${drawn} files in this group`);
    }
    if (stubs > 0) {
      // Where the faded ring is explained. It used to be said twice: here as a
      // count and again in the description as prose about "faded nodes".
      parts.push(
        `plus ${stubs} faded file${stubs === 1 ? "" : "s"} outside it that they reach`,
      );
    }
    const hidden = communitySlice.hidden_member_count ?? 0;
    if (hidden > 0) {
      parts.push(`${hidden} more hidden by the file filter`);
    }
    return `${parts.join(", ")}.`;
  })();

  const activeCommunityLabel =
    activeCommunity !== null
      ? (communityLabels?.get(activeCommunity) ?? `Community ${activeCommunity}`)
      : null;

  return (
    <GraphCanvasShell
      breadcrumb={
        isInsideCommunity && activeCommunityLabel ? (
          <GraphScopeBreadcrumb
            rootLabel={repoName ?? "All communities"}
            leafLabel={activeCommunityLabel}
            onRoot={leaveCommunity}
          />
        ) : undefined
      }
      description={
        // The breadcrumb above already names the community and carries the way
        // out, and the narrowing select names it a third time. Prose repeating
        // it is noise, so this says what the picture is, not what it is called.
        isInsideCommunity
          ? `How the files in this group depend on each other.${
              onNodeViewDocs ? " Double-click a file to open it." : ""
            }`
          : description
      }
      titleActions={
        <div className="flex flex-wrap items-center justify-end gap-2">
          {headerActions}
          {population && onPopulationChange && (isConstellation || isInsideCommunity) && (
            <GraphPopulationControl
              population={population}
              breakdown={constellationGraph?.population}
              onChange={onPopulationChange}
            />
          )}
          <GraphToolbar
            viewMode={viewMode}
            colorMode={colorMode}
            onColorModeChange={setColorMode}
            onFitView={handleFitView}
            showPathFinder={showPathFinder}
            pathFinderAvailable={Boolean(renderPathFinder)}
            onTogglePathFinder={() => {
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
            searchTotalCount={displayGraph?.order ?? 0}
            onSearchKeyDown={handleSearchKeyDown}
            layoutMode={isEnteringCommunity ? "radial" : layoutMode}
            onLayoutModeChange={handleLayoutModeChange}
            onToggleHelp={handleToggleShortcutHelp}
            hierarchicalDisabledReason={
              displayGraph && displayGraph.order > ELK_MAX_NODES
                ? elkSkipReason(displayGraph.order)
                : undefined
            }
          />
        </div>
      }
      banner={
        banner || layoutNotice || sliceNotice || pathNotice ? (
          <div className="space-y-2">
            {banner}
            {(sliceNotice || pathNotice) && (
              <p
                role="status"
                aria-live="polite"
                className="text-[11px] text-[var(--color-text-secondary)]"
              >
                {[sliceNotice, pathNotice].filter(Boolean).join(" ")}
              </p>
            )}
            {layoutNotice && (
              <div
                role="status"
                aria-live="polite"
                className="flex items-center gap-2 rounded-lg border border-[var(--color-warning)]/40 bg-[var(--color-bg-elevated)] px-3 py-1.5"
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
          </div>
        ) : undefined
      }
      rail={railContent}
      footer={
        <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2">
          <GraphLegend
            nodeCount={displayGraph?.order ?? 0}
            edgeCount={displayGraph?.size ?? 0}
            visibleEdgeCount={visibleEdgeCount}
            colorMode={colorMode}
            viewMode={viewMode}
            {...(isInsideCommunity && activeCommunityLabel
              ? {
                  scopeLabel: activeCommunityLabel,
                  scopeCommunityId: activeCommunity ?? undefined,
                  sliceCounts,
                }
              : {})}
            nodeFilter={
              // Withdrawn inside a community: the slice endpoint returns the
              // whole community whatever the signal says, so the pill lit, the
              // URL changed and the canvas did not.
              isConstellation || isInsideCommunity ? undefined : (
                <GraphNodeFilter
                  viewMode={viewMode}
                  onViewChange={handleViewChange}
                />
              )
            }
            {...(communityLabels ? { communityLabels } : {})}
            onCommunityClick={openCommunityPanel}
            activeCommunities={activeCommunities ?? undefined}
            drawnCommunityIds={drawnCommunityIds}
            onCommunityToggle={handleCommunityToggle}
            onToggleAllCommunities={handleToggleAllCommunities}
            visibleEdgeTypes={isConstellation ? undefined : visibleEdgeTypes}
            onEdgeTypeToggle={isConstellation ? undefined : handleEdgeTypeToggle}
            graphTheme={graphTheme}
            constellationEntries={isConstellation ? constellationLegend : undefined}
            onConstellationHubClick={handleConstellationHubClick}
            constellationUnclustered={
              isConstellation && constellationGraph?.unclustered
                ? {
                    count: constellationGraph.unclustered.file_count,
                    onClick: () => handleConstellationHubClick(UNCLUSTERED_COMMUNITY_ID),
                  }
                : undefined
            }
          />
          {hasCanvasStatus && (
            <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
              {isEgoActive && selectedNodeId ? (
                <span className="flex items-center gap-2 text-[11px] text-[var(--color-accent-primary)]">
                  <span role="status" aria-live="polite">
                    Showing {egoVisibleCount} nodes within {egoDepth} hop{egoDepth === 1 ? "" : "s"} of{" "}
                    <span className="font-mono font-medium">{selectedNodeId.split("/").pop()}</span>
                  </span>
                  <button
                    onClick={() => setEgoDepth(0)}
                    className="rounded px-1 py-0.5 text-[var(--color-text-tertiary)] underline hover:text-[var(--color-text-primary)]"
                  >
                    Clear
                  </button>
                </span>
              ) : null}
              {showOverlayCounts && overlayStats && (
                <span role="status" aria-live="polite" className="flex flex-wrap items-center gap-x-3">
                  {isDeadView && (
                    <OverlayCountChip kind="dead" inView={overlayStats.deadInView} total={deadTotal} />
                  )}
                  {isHotView && (
                    <OverlayCountChip kind="hot" inView={overlayStats.hotInView} total={hotTotal} />
                  )}
                </span>
              )}
            </div>
          )}
        </div>
      }
      overlay={
        <>
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
          {showShortcutHelp && (
            <GraphShortcutHelp onClose={() => setShowShortcutHelp(false)} />
          )}
        </>
      }
    >
      <div
        className="relative h-full w-full"
        style={{ touchAction: "none", ...(graphTheme === "dark" ? { background: "var(--color-bg-root)" } : {}) }}
        aria-label="Dependency graph"
      >
      {displayGraph && displayGraph.order > 0 ? (
        <SigmaCanvas
          ref={sigmaRef}
          graph={displayGraph}
          layoutMode={isEnteringCommunity ? "radial" : layoutMode}
          viewMode={viewMode}
          selectedNodeId={selectedNodeId}
          highlightedPath={highlightedPath}
          highlightedEdges={highlightedEdges}
          searchDimmedNodes={searchDimmedNodes}
          communityDimmedNodes={communityDimmedNodes}
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
          reducedMotion={prefersReducedMotion}
          hiddenNodes={isEgoActive ? hiddenNodes : undefined}
          visibleEdgeTypes={visibleEdgeTypes}
          depthRingRadii={
            isConstellation || isEnteringCommunity ? constellationRingRadii : null
          }
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
      </div>
    </GraphCanvasShell>
  );
}

/** Caption for a dead/hot view: "12 of 37 dead files in view" when the backend
 *  supplies repo-wide totals, or just the in-view count when it does not. */
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
    text = `${inView} of ${total} ${noun} in view; the rest are outside the loaded node set`;
  } else if (total != null) {
    text = `Showing all ${total} ${noun}`;
  } else {
    text = `${inView} ${noun} in view`;
  }
  // No role here: the footer wrapper is already the live region, and nesting
  // one inside another gets the text announced twice.
  return <span className="text-[11px] text-[var(--color-text-secondary)]">{text}</span>;
}
