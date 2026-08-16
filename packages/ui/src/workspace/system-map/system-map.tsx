"use client";

/**
 * The Live System Map — a code-derived, always-current diagram of the
 * workspace's services (nodes) and their typed relationships (edges). Pure
 * presentation over the Phase 1 `SystemGraph`: no computation, no fetching.
 * The host passes the graph (and an optional repo-health join); this renders
 * it with the shared ELK + React Flow stack, edge-kind filters, a legend, and
 * a node/edge inspector. Phases 3-5 decorate it via the additive `overlay`
 * prop without forking this component.
 */

import { useCallback, useMemo, useRef, useState } from "react";
import {
  Background,
  BackgroundVariant,
  Controls,
  MiniMap,
  ReactFlow,
  ReactFlowProvider,
  type EdgeMouseHandler,
  type NodeMouseHandler,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import type { NodeArchitectureRole, SystemEdgeKind, SystemGraph } from "@repowise-dev/types";
import { EmptyState } from "../../shared/empty-state";
import { systemMapNodeTypes } from "./system-map-node";
import { systemMapEdgeTypes } from "./system-map-edge";
import { SystemMapLegend } from "./system-map-legend";
import { SystemMapFilters } from "./system-map-filters";
import { SystemMapInspector } from "./system-map-inspector";
import { useSystemMapLayout } from "./use-system-map-layout";
import { SYSTEM_MAP_MAX_LAYOUT_NODES, type SystemMapView } from "./layout";
import type { RepoHealth, SystemMapOverlay, SystemMapSelection } from "./types";
import { toFriendlyMessage } from "../../lib/errors";

export interface SystemMapProps {
  graph: SystemGraph | null;
  loading?: boolean;
  error?: Error | null;
  /** Repo health by repo alias, joined onto service nodes (optional). */
  healthByRepo?: ReadonlyMap<string, RepoHealth>;
  /** Additive decoration from a later phase (ripple, badges, violations). */
  overlay?: SystemMapOverlay;
  /** Per-service architecture role + visibility, shown in the inspector (optional). */
  roleByNodeId?: ReadonlyMap<string, NodeArchitectureRole>;
  /** Open a contract on the Contracts surface (edge drill-down). */
  onOpenContract?: (contractId: string) => void;
  /**
   * Host-owned cards for the map's rail (blast radius, breaking changes,
   * conformance). They share the rail with the inspector and its scroll
   * region, which is what stops them landing on the canvas or on each other.
   * Pass `null` when none are active so the rail column collapses.
   */
  rail?: React.ReactNode;
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

function SystemMapInner({
  graph,
  loading,
  error,
  healthByRepo,
  overlay,
  roleByNodeId,
  onOpenContract,
  rail,
  selection: controlledSelection,
  onSelectionChange,
}: SystemMapProps) {
  const availableKinds = useMemo<Set<SystemEdgeKind>>(
    () => new Set((graph?.edges ?? []).map((e) => e.kind)),
    [graph],
  );

  const [hiddenKinds, setHiddenKinds] = useState<Set<SystemEdgeKind>>(() => new Set());
  const [collapsed, setCollapsed] = useState(false);
  const [uncontrolledSelection, setUncontrolledSelection] = useState<SystemMapSelection>(null);
  const isControlled = onSelectionChange !== undefined;
  const selection = isControlled ? (controlledSelection ?? null) : uncontrolledSelection;

  // The click handlers below are memoised for the component's lifetime, so
  // `setSelection` has to be stable too — a version that closed over the current
  // props would be captured once and then toggle against, and notify, whatever
  // was live at mount. Read the controlled props through a ref instead.
  const latest = useRef({ controlledSelection, onSelectionChange });
  latest.current = { controlledSelection, onSelectionChange };

  const setSelection = useCallback(
    (next: SystemMapSelection | ((cur: SystemMapSelection) => SystemMapSelection)) => {
      const { controlledSelection: current, onSelectionChange: notify } = latest.current;
      if (notify) {
        notify(typeof next === "function" ? next(current ?? null) : next);
      } else {
        setUncontrolledSelection(next);
      }
    },
    [],
  );

  const visibleKinds = useMemo<Set<SystemEdgeKind>>(
    () => new Set([...availableKinds].filter((k) => !hiddenKinds.has(k))),
    [availableKinds, hiddenKinds],
  );

  const view = useMemo<SystemMapView>(() => ({ visibleKinds, collapsed }), [visibleKinds, collapsed]);

  const { nodes, edges, loading: layoutLoading, viewGraph, simplified } = useSystemMapLayout({
    graph,
    view,
    ...(healthByRepo ? { healthByRepo } : {}),
    ...(overlay ? { overlay } : {}),
  });

  const toggleKind = useCallback((kind: SystemEdgeKind) => {
    setHiddenKinds((prev) => {
      const next = new Set(prev);
      if (next.has(kind)) next.delete(kind);
      else next.add(kind);
      return next;
    });
  }, []);

  const onNodeClick = useCallback<NodeMouseHandler>((_, node) => {
    setSelection((cur) => (cur?.type === "node" && cur.id === node.id ? null : { type: "node", id: node.id }));
  }, []);

  const onEdgeClick = useCallback<EdgeMouseHandler>((_, edge) => {
    setSelection((cur) => (cur?.type === "edge" && cur.id === edge.id ? null : { type: "edge", id: edge.id }));
  }, []);

  const onPaneClick = useCallback(() => setSelection(null), []);
  const selectNode = useCallback((id: string) => setSelection({ type: "node", id }), []);

  // Collapse selection is keyed by id; collapsing changes node ids, so reset it.
  const onToggleCollapsed = useCallback(() => {
    setCollapsed((c) => !c);
    setSelection(null);
  }, []);

  const isLoading = loading || layoutLoading;
  const hasGraph = graph && graph.nodes.length > 0;
  const hasEdges = (graph?.edges.length ?? 0) > 0;

  const hasRail = Boolean(rail) || selection !== null;

  return (
    <div className="flex h-full w-full flex-col bg-[var(--color-bg-canvas)]">
      {/* Lens control in the section header: it acts on the canvas, so it sits
          above it rather than on it. */}
      <div className="flex flex-wrap items-center gap-4 border-b border-[var(--color-border-default)] bg-[var(--color-bg-surface)] px-3 py-2">
        <SystemMapFilters
          availableKinds={availableKinds}
          visibleKinds={visibleKinds}
          onToggleKind={toggleKind}
          collapsed={collapsed}
          onToggleCollapsed={onToggleCollapsed}
        />
      </div>

      {/* Canvas and rail are grid peers, so a panel can never land on the map.
          Below `lg` the rail relocates under the canvas rather than covering
          it. */}
      <div
        className={`grid min-h-0 flex-1 gap-4 p-3 ${
          hasRail ? "grid-cols-1 lg:grid-cols-[minmax(0,1fr)_320px]" : "grid-cols-1"
        }`}
      >
        <div className="flex min-h-0 flex-col gap-2">
          <div className="relative min-h-0 flex-1">
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
              description="Services are indexed, but no HTTP, gRPC, event, package, or co-change links were found between them yet."
            />
          </Centered>
        ) : null}

        <ReactFlow
          nodes={nodes}
          edges={edges}
          nodeTypes={systemMapNodeTypes}
          edgeTypes={systemMapEdgeTypes}
          onNodeClick={onNodeClick}
          onEdgeClick={onEdgeClick}
          onPaneClick={onPaneClick}
          fitView
          fitViewOptions={{ padding: 0.18 }}
          minZoom={0.2}
          maxZoom={2.5}
          proOptions={{ hideAttribution: true }}
          nodesDraggable={false}
          nodesConnectable={false}
        >
          <Background variant={BackgroundVariant.Lines} gap={24} size={1} color="var(--color-diagram-grid)" />
          <Controls showInteractive={false} />
          <MiniMap pannable zoomable />
        </ReactFlow>
          </div>

          {/* The key reads as a caption under the field, not as a card on it. */}
          <SystemMapLegend />

          {simplified && (
            <p className="px-1 text-[11px] text-[var(--color-warning)]">
              {`Layout simplified: above ${SYSTEM_MAP_MAX_LAYOUT_NODES} services the map falls back to a grid, so node placement no longer reflects dependency direction.`}
            </p>
          )}
        </div>

        {hasRail && (
          <aside className="flex min-h-0 flex-col gap-3 overflow-y-auto lg:max-h-full">
            {viewGraph && (
              <SystemMapInspector
                selection={selection}
                graph={viewGraph}
                {...(healthByRepo ? { healthByRepo } : {})}
                {...(roleByNodeId ? { roleByNodeId } : {})}
                onClose={() => setSelection(null)}
                onSelectNode={selectNode}
                {...(onOpenContract ? { onOpenContract } : {})}
              />
            )}
            {rail}
          </aside>
        )}
      </div>
    </div>
  );
}

/**
 * Empty/error state centred over the canvas box. This one is deliberately
 * absolute: it is the canvas's own "nothing to draw" copy, not chrome competing
 * with a diagram that is there to be read.
 */
function Centered({ children }: { children: React.ReactNode }) {
  return (
    <div className="pointer-events-none absolute inset-0 z-[3] flex items-center justify-center">
      {children}
    </div>
  );
}
