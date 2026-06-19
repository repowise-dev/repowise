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

import { useCallback, useMemo, useState } from "react";
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
import type { SystemMapView } from "./layout";
import type { RepoHealth, SystemMapOverlay, SystemMapSelection } from "./types";

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
}

export function SystemMap(props: SystemMapProps) {
  return (
    <ReactFlowProvider>
      <SystemMapInner {...props} />
    </ReactFlowProvider>
  );
}

function SystemMapInner({ graph, loading, error, healthByRepo, overlay, roleByNodeId, onOpenContract }: SystemMapProps) {
  const availableKinds = useMemo<Set<SystemEdgeKind>>(
    () => new Set((graph?.edges ?? []).map((e) => e.kind)),
    [graph],
  );

  const [hiddenKinds, setHiddenKinds] = useState<Set<SystemEdgeKind>>(() => new Set());
  const [collapsed, setCollapsed] = useState(false);
  const [selection, setSelection] = useState<SystemMapSelection>(null);

  const visibleKinds = useMemo<Set<SystemEdgeKind>>(
    () => new Set([...availableKinds].filter((k) => !hiddenKinds.has(k))),
    [availableKinds, hiddenKinds],
  );

  const view = useMemo<SystemMapView>(() => ({ visibleKinds, collapsed }), [visibleKinds, collapsed]);

  const { nodes, edges, loading: layoutLoading } = useSystemMapLayout({
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

  return (
    <div style={{ position: "relative", width: "100%", height: "100%", display: "flex", flexDirection: "column", background: "var(--color-bg-canvas)" }}>
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 16,
          padding: "8px 12px",
          borderBottom: "1px solid var(--color-border-default)",
          background: "var(--color-bg-surface)",
          flexWrap: "wrap",
        }}
      >
        <SystemMapFilters
          availableKinds={availableKinds}
          visibleKinds={visibleKinds}
          onToggleKind={toggleKind}
          collapsed={collapsed}
          onToggleCollapsed={onToggleCollapsed}
        />
        <div style={{ marginLeft: "auto" }}>
          <SystemMapLegend />
        </div>
      </div>

      <div style={{ position: "relative", flex: 1, minHeight: 0 }}>
        {error ? (
          <Centered>
            <EmptyState title="Couldn't load the system map" description={error.message} />
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

        {graph && (
          <SystemMapInspector
            selection={selection}
            graph={graph}
            {...(healthByRepo ? { healthByRepo } : {})}
            {...(roleByNodeId ? { roleByNodeId } : {})}
            onClose={() => setSelection(null)}
            onSelectNode={selectNode}
            {...(onOpenContract ? { onOpenContract } : {})}
          />
        )}
      </div>
    </div>
  );
}

function Centered({ children }: { children: React.ReactNode }) {
  return (
    <div style={{ position: "absolute", inset: 0, display: "flex", alignItems: "center", justifyContent: "center", zIndex: 3, pointerEvents: "none" }}>
      {children}
    </div>
  );
}
