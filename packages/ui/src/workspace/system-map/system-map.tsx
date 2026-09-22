"use client";

/**
 * The Live System Map: a code-derived diagram of the workspace's services
 * (nodes) and their typed relationships (edges), laid out with the shared ELK
 * stack and explored through a detail drawer.
 *
 * A canvas surface: the controls sit in the section header, the key in one
 * row under them, and the canvas takes the width. The host supplies the graph,
 * the lens control (`toolbar`) and the drawer's data; this component owns the
 * edge-kind filter, the service/repository switch, hover and selection.
 */

import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import {
  Background,
  BackgroundVariant,
  Controls,
  MiniMap,
  ReactFlow,
  ReactFlowProvider,
  useReactFlow,
  useStore,
  type EdgeChange,
  type EdgeMouseHandler,
  type NodeChange,
  type NodeMouseHandler,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import type { NodeArchitectureRole, SystemEdgeKind, SystemEdgeMatchType, SystemGraph } from "@repowise-dev/types";
import { EmptyState } from "../../shared/empty-state";
import { systemMapNodeTypes } from "./system-map-node";
import { systemMapEdgeTypes } from "./system-map-edge";
import { SystemMapLegend } from "./system-map-legend";
import { SystemMapFilters } from "./system-map-filters";
import { SystemMapDrawer, type SystemMapDrawerData } from "./system-map-drawer";
import { SystemMapFocusContext, createSystemMapFocusStore } from "./system-map-focus";
import { edgeKindStyle } from "./edge-kinds";
import { useSystemMapLayout } from "./use-system-map-layout";
import { resolveViewSelection } from "./system-map-model";
import { SYSTEM_MAP_MAX_LAYOUT_NODES, SYSTEM_MAP_NODE_SIZE, applyCollapse, type SystemMapView } from "./layout";
import type { RepoHealth, SystemMapOverlay, SystemMapSelection } from "./types";
import { toFriendlyMessage } from "../../lib/errors";

/** Below this many nodes the whole map fits on screen and a minimap is noise. */
export const SYSTEM_MAP_MINIMAP_MIN_NODES = 40;

/** The canvas's height, shared with the host's loading placeholder so nothing reflows. */
export const SYSTEM_MAP_CANVAS_HEIGHT = "h-[420px] sm:h-[clamp(460px,calc(100dvh-240px),800px)]";

/** Width the drawer takes on desktop; selections are kept clear of it. */
const DRAWER_WIDTH = 480;

const FIT_OPTIONS = { padding: 0.08, maxZoom: 1.2 } as const;

export interface SystemMapProps {
  graph: SystemGraph | null;
  loading?: boolean;
  error?: Error | null;
  /** Repo health by repo alias, joined onto service nodes (optional). */
  healthByRepo?: ReadonlyMap<string, RepoHealth>;
  /** Additive decoration from the active lens (ripple, badges, violations). */
  overlay?: SystemMapOverlay;
  /** Per-service architecture role, named on nodes and in the drawer (optional). */
  roleByNodeId?: ReadonlyMap<string, NodeArchitectureRole>;
  /** The lens control, placed at the head of the section header. */
  toolbar?: ReactNode;
  /** Data and routes for the detail drawer. */
  drawer?: SystemMapDrawerData;
  /** Controlled selection, so the host can put it in the URL. */
  selection?: SystemMapSelection;
  onSelectionChange?: (selection: SystemMapSelection) => void;
}

export function SystemMap(props: SystemMapProps) {
  return (
    <ReactFlowProvider>
      <SystemMapInner {...props} />
    </ReactFlowProvider>
  );
}

function sameSelection(a: SystemMapSelection, b: SystemMapSelection): boolean {
  return a?.type === b?.type && a?.id === b?.id;
}

function SystemMapInner({
  graph,
  loading,
  error,
  healthByRepo,
  overlay,
  roleByNodeId,
  toolbar,
  drawer,
  selection: controlledSelection,
  onSelectionChange,
}: SystemMapProps) {
  const [hiddenKinds, setHiddenKinds] = useState<Set<SystemEdgeKind>>(() => new Set());
  const [collapsed, setCollapsed] = useState(false);
  const [uncontrolledSelection, setUncontrolledSelection] = useState<SystemMapSelection>(null);
  const isControlled = onSelectionChange !== undefined;
  const selection = isControlled ? (controlledSelection ?? null) : uncontrolledSelection;

  // Handlers below are memoised for the component's lifetime, so they read the
  // live props through a ref. A click also fires React Flow's own selection
  // change; `last` drops the duplicate so the host sees one update.
  const latest = useRef({ selection, onSelectionChange });
  latest.current = { selection, onSelectionChange };
  const last = useRef<SystemMapSelection>(selection);
  last.current = selection;

  const setSelection = useCallback((next: SystemMapSelection) => {
    if (sameSelection(last.current, next)) return;
    last.current = next;
    const notify = latest.current.onSelectionChange;
    if (notify) notify(next);
    else setUncontrolledSelection(next);
  }, []);

  // The collapse-applied shape, before the kind filter: what the filter offers.
  const shaped = useMemo(() => (graph ? applyCollapse(graph, collapsed) : null), [graph, collapsed]);
  const kindCounts = useMemo(() => {
    const m = new Map<SystemEdgeKind, number>();
    for (const e of shaped?.edges ?? []) m.set(e.kind, (m.get(e.kind) ?? 0) + 1);
    return m;
  }, [shaped]);
  const visibleKinds = useMemo<Set<SystemEdgeKind>>(
    () => new Set([...kindCounts.keys()].filter((k) => !hiddenKinds.has(k))),
    [kindCounts, hiddenKinds],
  );
  const view = useMemo<SystemMapView>(() => ({ visibleKinds, collapsed }), [visibleKinds, collapsed]);

  const { nodes, edges, loading: layoutLoading, viewGraph, simplified } = useSystemMapLayout({
    graph,
    view,
    ...(healthByRepo ? { healthByRepo } : {}),
    // Overlays are keyed by service; on merged repo nodes only the repo roots
    // would match, which marks some repositories and not others. Off instead.
    ...(overlay && !collapsed ? { overlay } : {}),
    ...(roleByNodeId ? { roleByNodeId } : {}),
  });

  // Lists and the URL name raw ids; map them onto what is drawn, and show a
  // hidden kind or the service view when that is the only way to draw them.
  const resolved = useMemo(
    () =>
      graph && viewGraph
        ? resolveViewSelection(selection, graph, viewGraph, collapsed)
        : { selection: null, fix: null },
    [selection, graph, viewGraph, collapsed],
  );
  const drawnSelection = resolved.selection;
  const fixKey = resolved.fix ? `${resolved.fix.kind}:${"edgeKind" in resolved.fix ? resolved.fix.edgeKind : ""}` : "";
  useEffect(() => {
    const fix = resolved.fix;
    if (!fix) return;
    if (fix.kind === "expand") setCollapsed(false);
    else
      setHiddenKinds((prev) => {
        if (!prev.has(fix.edgeKind)) return prev;
        const next = new Set(prev);
        next.delete(fix.edgeKind);
        return next;
      });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [fixKey]);

  // Only the selected node gets a new object, so React Flow re-renders one node.
  const selectedNode = drawnSelection?.type === "node" ? drawnSelection.id : null;
  const drawnNodes = useMemo(
    () => (selectedNode ? nodes.map((n) => (n.id === selectedNode ? { ...n, selected: true } : n)) : nodes),
    [nodes, selectedNode],
  );

  const matchTypes = useMemo(
    () => new Set<SystemEdgeMatchType>((viewGraph?.edges ?? []).map((e) => e.match_type)),
    [viewGraph],
  );

  // Hover and selection for edges live outside React state (see system-map-focus).
  const focus = useMemo(() => createSystemMapFocusStore(), []);
  const canvasRef = useRef<HTMLDivElement>(null);
  useEffect(
    () =>
      focus.subscribe(() => {
        canvasRef.current?.toggleAttribute("data-focused", focus.focused());
      }),
    [focus],
  );
  useEffect(() => {
    focus.setSelected({
      node: drawnSelection?.type === "node" ? drawnSelection.id : null,
      edge: drawnSelection?.type === "edge" ? drawnSelection.id : null,
    });
  }, [focus, drawnSelection]);

  const toggleKind = useCallback((kind: SystemEdgeKind) => {
    setHiddenKinds((prev) => {
      const next = new Set(prev);
      if (next.has(kind)) next.delete(kind);
      else next.add(kind);
      return next;
    });
  }, []);

  const onNodeClick = useCallback<NodeMouseHandler>((_, node) => setSelection({ type: "node", id: node.id }), [setSelection]);
  const onEdgeClick = useCallback<EdgeMouseHandler>((_, edge) => setSelection({ type: "edge", id: edge.id }), [setSelection]);
  // Keyboard selection (Enter or Space on a focused node or edge) arrives as a
  // React Flow selection change, and lands on the same object a click would.
  const onNodesChange = useCallback(
    (changes: NodeChange[]) => {
      for (const c of changes) if (c.type === "select" && c.selected) setSelection({ type: "node", id: c.id });
    },
    [setSelection],
  );
  const onEdgesChange = useCallback(
    (changes: EdgeChange[]) => {
      for (const c of changes) if (c.type === "select" && c.selected) setSelection({ type: "edge", id: c.id });
    },
    [setSelection],
  );
  const onPaneClick = useCallback(() => setSelection(null), [setSelection]);
  const onNodeMouseEnter = useCallback<NodeMouseHandler>((_, n) => focus.setHover({ node: n.id }), [focus]);
  const onNodeMouseLeave = useCallback<NodeMouseHandler>(() => focus.setHover({ node: null }), [focus]);
  const onEdgeMouseEnter = useCallback<EdgeMouseHandler>((_, e) => focus.setHover({ edge: e.id }), [focus]);
  const onEdgeMouseLeave = useCallback<EdgeMouseHandler>(() => focus.setHover({ edge: null }), [focus]);

  // Collapsing changes node ids, so a selection cannot survive it.
  const onCollapsedChange = useCallback(
    (next: boolean) => {
      setCollapsed(next);
      setSelection(null);
    },
    [setSelection],
  );

  const isLoading = loading || layoutLoading;
  const hasGraph = Boolean(graph && graph.nodes.length > 0);
  const hasEdges = (graph?.edges.length ?? 0) > 0;
  const canCollapse = Boolean(graph?.nodes.some((n) => n.service_path));

  return (
    <SystemMapFocusContext.Provider value={focus}>
      <div className="flex min-w-0 flex-col gap-3">
        {/* Section header: the lens, then what is drawn. Nothing sits on the canvas. */}
        <div className="flex min-w-0 flex-wrap items-center justify-between gap-x-6 gap-y-3">
          {toolbar}
          <SystemMapFilters
            kindCounts={kindCounts}
            visibleKinds={visibleKinds}
            onToggleKind={toggleKind}
            canCollapse={canCollapse}
            collapsed={collapsed}
            onCollapsedChange={onCollapsedChange}
          />
        </div>

        <SystemMapLegend matchTypes={matchTypes} />

        <div
          ref={canvasRef}
          className={`sm-canvas relative min-w-0 overflow-hidden rounded-lg border border-[var(--color-border-default)] bg-[var(--color-bg-root)] ${SYSTEM_MAP_CANVAS_HEIGHT}`}
        >
          <style>{".sm-canvas[data-focused] .sm-edge:not([data-active]){opacity:.22}.sm-canvas .sm-edge{transition:opacity 120ms}"}</style>
          {error ? (
            <Centered>
              <EmptyState title="Couldn't load the system map" description={toFriendlyMessage(error)} />
            </Centered>
          ) : !isLoading && !hasGraph ? (
            <Centered>
              <EmptyState
                title="No services to map yet"
                description="The system map appears once the workspace has at least two indexed repositories with detected cross-repo relationships."
              />
            </Centered>
          ) : !isLoading && !hasEdges ? (
            <Centered>
              <EmptyState
                title="No cross-repo relationships detected"
                description="Services are indexed, but no HTTP, gRPC, event, package, database or co-change links were found between them yet."
              />
            </Centered>
          ) : null}

          <ReactFlow
            nodes={drawnNodes}
            edges={edges}
            nodeTypes={systemMapNodeTypes}
            edgeTypes={systemMapEdgeTypes}
            onNodeClick={onNodeClick}
            onEdgeClick={onEdgeClick}
            onNodesChange={onNodesChange}
            onEdgesChange={onEdgesChange}
            onPaneClick={onPaneClick}
            onNodeMouseEnter={onNodeMouseEnter}
            onNodeMouseLeave={onNodeMouseLeave}
            onEdgeMouseEnter={onEdgeMouseEnter}
            onEdgeMouseLeave={onEdgeMouseLeave}
            fitView
            fitViewOptions={FIT_OPTIONS}
            minZoom={0.15}
            maxZoom={2}
            proOptions={{ hideAttribution: true }}
            nodesDraggable={false}
            nodesConnectable={false}
            ariaLabelConfig={{ "node.a11yDescription.default": "Press Enter to open this service's details." }}
          >
            <Background variant={BackgroundVariant.Dots} gap={20} size={1} color="var(--color-border-hover)" />
            <Controls showInteractive={false} />
            {nodes.length >= SYSTEM_MAP_MINIMAP_MIN_NODES && (
              <MiniMap
                pannable
                zoomable
                bgColor="var(--color-bg-surface)"
                maskColor="color-mix(in srgb, var(--color-bg-root) 70%, transparent)"
                nodeColor="var(--color-border-hover)"
                nodeStrokeColor="var(--color-text-tertiary)"
                style={{ border: "1px solid var(--color-border-default)" }}
              />
            )}
            <Viewport fitKey={`${collapsed}:${nodes.length}`} ready={nodes.length > 0} selection={drawnSelection} canvas={canvasRef} />
          </ReactFlow>
        </div>

        {graph && viewGraph && <ScopeCaption graph={graph} viewGraph={viewGraph} collapsed={collapsed} lensed={Boolean(overlay)} hidden={hiddenKinds} kindCounts={kindCounts} simplified={simplified} />}

        {graph && viewGraph && (
          <SystemMapDrawer
            {...drawer}
            selection={drawnSelection}
            graph={viewGraph}
            rawGraph={graph}
            collapsed={collapsed}
            healthByRepo={healthByRepo}
            roleByNodeId={roleByNodeId}
            onClose={() => setSelection(null)}
            onSelect={setSelection}
          />
        )}
      </div>
    </SystemMapFocusContext.Provider>
  );
}

/**
 * Owns the viewport. Fits the drawing once the layout lands and when the canvas
 * resizes (`fitView` on mount alone fit an empty field, because the async
 * layout places nodes after React Flow's first pass). Then, on desktop, where
 * the drawer covers the canvas's right edge, pans the selection into the
 * uncovered part without changing the zoom. A selection from a list below the
 * map, or from the URL, would otherwise open a drawer about something hidden.
 */
function Viewport({
  fitKey,
  ready,
  selection,
  canvas,
}: {
  fitKey: string;
  ready: boolean;
  selection: SystemMapSelection;
  canvas: React.RefObject<HTMLDivElement | null>;
}) {
  const rf = useReactFlow();
  // Not useNodesInitialized: controlled nodes that ignore dimension changes
  // never report it. The layout's node count is the signal instead.
  const initialized = ready;
  const width = useStore((s) => s.width);
  const height = useStore((s) => s.height);
  const latestSelection = useRef(selection);
  latestSelection.current = selection;

  const reveal = useCallback(
    (sel: SystemMapSelection) => {
      if (!sel || typeof window === "undefined" || window.innerWidth < 768) return;
      const el = canvas.current;
      if (!el) return;
      const ids =
        sel.type === "node" ? [sel.id] : (() => {
          const e = rf.getEdge(sel.id);
          return e ? [e.source, e.target] : [];
        })();
      const points = ids
        .map((id) => rf.getInternalNode(id))
        .filter((n): n is NonNullable<typeof n> => Boolean(n))
        .map((n) => ({
          x: n.internals.positionAbsolute.x + SYSTEM_MAP_NODE_SIZE.width / 2,
          y: n.internals.positionAbsolute.y + SYSTEM_MAP_NODE_SIZE.height / 2,
        }));
      if (points.length === 0) return;
      const center = {
        x: points.reduce((a, p) => a + p.x, 0) / points.length,
        y: points.reduce((a, p) => a + p.y, 0) / points.length,
      };
      const rect = el.getBoundingClientRect();
      const visibleRight = Math.max(Math.min(rect.right, window.innerWidth - DRAWER_WIDTH), rect.left + 240);
      const screen = rf.flowToScreenPosition(center);
      const margin = SYSTEM_MAP_NODE_SIZE.width / 2;
      const inView =
        screen.x > rect.left + margin &&
        screen.x < visibleRight - margin &&
        screen.y > rect.top + 40 &&
        screen.y < rect.bottom - 40;
      if (inView) return;
      const { zoom } = rf.getViewport();
      // Only move on the axis that hides it, so the map shifts as little as possible.
      const xInView = screen.x > rect.left + margin && screen.x < visibleRight - margin;
      const targetX = xInView ? screen.x - rect.left : (visibleRight - rect.left) / 2;
      const yInView = screen.y > rect.top + 40 && screen.y < rect.bottom - 40;
      const targetY = yInView ? screen.y - rect.top : rect.height / 2;
      void rf.setViewport({ x: targetX - center.x * zoom, y: targetY - center.y * zoom, zoom }, { duration: 250 });
    },
    [rf, canvas],
  );

  useEffect(() => {
    if (!initialized || width === 0 || height === 0) return;
    // fitView is queued inside React Flow, so the reveal waits a beat for the
    // fitted viewport rather than reading the one it replaces.
    let reveal2 = 0;
    const id = requestAnimationFrame(() => {
      void rf.fitView(FIT_OPTIONS);
      reveal2 = window.setTimeout(() => reveal(latestSelection.current), 80);
    });
    return () => {
      cancelAnimationFrame(id);
      window.clearTimeout(reveal2);
    };
  }, [rf, initialized, fitKey, width, height, reveal]);

  const selKey = selection ? `${selection.type}:${selection.id}` : "";
  useEffect(() => {
    if (initialized) reveal(latestSelection.current);
  }, [selKey, initialized, reveal]);

  return null;
}

/** What is drawn and what is not, stated beside the canvas. */
function ScopeCaption({
  graph,
  viewGraph,
  collapsed,
  lensed,
  hidden,
  kindCounts,
  simplified,
}: {
  graph: SystemGraph;
  viewGraph: SystemGraph;
  collapsed: boolean;
  lensed: boolean;
  hidden: ReadonlySet<SystemEdgeKind>;
  kindCounts: ReadonlyMap<SystemEdgeKind, number>;
  simplified: boolean;
}) {
  const repoOf = new Map(graph.nodes.map((n) => [n.id, n.repo]));
  const intraRepo = graph.edges.filter((e) => repoOf.get(e.source) === repoOf.get(e.target)).length;
  const hiddenParts = [...hidden]
    .filter((k) => (kindCounts.get(k) ?? 0) > 0)
    .map((k) => `${kindCounts.get(k)} ${edgeKindStyle(k).label}`);
  const nodes = viewGraph.nodes.length;
  const edges = viewGraph.edges.length;

  const parts = [
    collapsed
      ? `Drawing ${nodes} ${nodes === 1 ? "repository" : "repositories"} and ${edges} ${edges === 1 ? "relationship" : "relationships"}. Edges of one kind between the same two repositories are merged, and ${intraRepo} ${intraRepo === 1 ? "relationship" : "relationships"} inside a repository ${intraRepo === 1 ? "is" : "are"} not drawn.`
      : `Drawing ${nodes} ${nodes === 1 ? "service" : "services"} and ${edges} ${edges === 1 ? "relationship" : "relationships"}.`,
    hiddenParts.length > 0 ? `Hidden by the edge filter: ${hiddenParts.join(", ")}.` : null,
    // Lens results name services, and a merged node is several of them.
    collapsed && lensed ? "Lens marks are drawn per service, so they are off in this view; switch to Services to see them." : null,
    "Placement follows structural dependencies; co-change lines do not move services.",
  ];

  return (
    <div className="flex flex-col gap-1">
      <p className="text-xs text-[var(--color-text-tertiary)]">{parts.filter(Boolean).join(" ")}</p>
      {simplified && (
        <p className="text-xs text-[var(--color-warning)]">
          {`Layout simplified: above ${SYSTEM_MAP_MAX_LAYOUT_NODES} services the map falls back to a grid, so placement no longer reflects dependency direction.`}
        </p>
      )}
    </div>
  );
}

/**
 * Empty/error state centred over the canvas box. Deliberately absolute: it is
 * the canvas's own "nothing to draw" copy, not chrome competing with a diagram.
 */
function Centered({ children }: { children: React.ReactNode }) {
  return (
    <div className="pointer-events-none absolute inset-0 z-[3] flex items-center justify-center">
      {children}
    </div>
  );
}
