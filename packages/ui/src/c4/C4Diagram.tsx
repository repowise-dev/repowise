"use client";

/**
 * Top-level C4 diagram view.
 *
 * Pure presentational + interaction shell. The host page is responsible for
 * fetching `l1View`/`l2View`/`l3View` (typically with SWR per level) and
 * passing the active one in. State (level / active container / selection) is
 * either lifted from the host via props OR managed locally via `useC4Store`.
 */

import { useCallback, useMemo, useState } from "react";
import {
  Background,
  Controls,
  MiniMap,
  ReactFlow,
  ReactFlowProvider,
  type Node,
  type NodeMouseHandler,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { c4NodeTypes } from "./nodes";
import { c4EdgeTypes } from "./edges/RelationEdge";
import {
  C4Breadcrumb,
  C4Legend,
  C4LevelTabs,
  C4NodeInspector,
} from "./panels";
import { useC4Layout } from "./hooks/use-c4-layout";
import { useC4Keyboard } from "./hooks/use-c4-keyboard";
import type {
  C4L1,
  C4L2,
  C4L3,
  C4Level,
  C4NodeData,
} from "./types";

export interface C4DiagramProps {
  level: C4Level;
  activeContainerId: string | null;
  systemName: string;

  l1View: C4L1 | null;
  l2View: C4L2 | null;
  l3View: C4L3 | null;

  loading?: boolean;
  error?: Error | null;

  onLevelChange: (level: C4Level) => void;
  onDrillInto: (containerId: string) => void;
  onDrillOut: () => void;
}

export function C4Diagram(props: C4DiagramProps) {
  return (
    <ReactFlowProvider>
      <C4DiagramInner {...props} />
    </ReactFlowProvider>
  );
}

function C4DiagramInner({
  level,
  activeContainerId,
  systemName,
  l1View,
  l2View,
  l3View,
  loading,
  error,
  onLevelChange,
  onDrillInto,
  onDrillOut,
}: C4DiagramProps) {
  const view = level === 1 ? l1View : level === 2 ? l2View : l3View;
  const { nodes, edges, loading: layoutLoading } = useC4Layout(level, view);

  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);
  const selectedNode = useMemo(
    () => (selectedNodeId ? nodes.find((n) => n.id === selectedNodeId) ?? null : null),
    [nodes, selectedNodeId],
  );
  const selectedData = (selectedNode?.data as unknown as C4NodeData | undefined) ?? null;

  useC4Keyboard(useCallback(() => {
    if (selectedNodeId) {
      setSelectedNodeId(null);
    } else {
      onDrillOut();
    }
  }, [onDrillOut, selectedNodeId]));

  const handleNodeClick: NodeMouseHandler = useCallback((_, node) => {
    setSelectedNodeId((cur) => (cur === node.id ? null : node.id));
  }, []);

  const handleNodeDoubleClick: NodeMouseHandler = useCallback(
    (_, node: Node) => {
      const data = node.data as unknown as C4NodeData;
      if (data.kind === "container") {
        onDrillInto(data.container.id);
        setSelectedNodeId(null);
      }
    },
    [onDrillInto],
  );

  const activeContainerPath = useMemo(() => {
    if (level !== 3 || !activeContainerId) return null;
    if (l3View?.container.id === activeContainerId) return l3View.container.path;
    const found = l2View?.containers.find((c) => c.id === activeContainerId);
    return found?.path ?? activeContainerId.replace(/^pkg:/, "");
  }, [activeContainerId, l2View, l3View, level]);

  const isLoading = loading || layoutLoading;

  return (
    <div
      style={{
        position: "relative",
        width: "100%",
        height: "100%",
        display: "flex",
        flexDirection: "column",
        background: "var(--color-bg-canvas, #0b1220)",
      }}
    >
      {/* Toolbar row */}
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 16,
          padding: "8px 12px",
          borderBottom: "1px solid var(--color-border-default, #334155)",
          background: "var(--color-bg-surface, rgba(15,23,42,0.6))",
        }}
      >
        <C4LevelTabs
          level={level}
          onLevelChange={onLevelChange}
          l3Enabled={activeContainerId != null || level === 3}
        />
        <div style={{ width: 1, height: 20, background: "var(--color-border-default, #334155)" }} />
        <C4Breadcrumb
          level={level}
          systemName={systemName}
          activeContainerPath={activeContainerPath}
          onNavigate={onLevelChange}
        />
        <div style={{ marginLeft: "auto" }}>
          <C4Legend />
        </div>
      </div>

      {/* Canvas */}
      <div style={{ position: "relative", flex: 1, minHeight: 0 }}>
        {error && <CenteredMessage tone="error" text={error.message} />}
        {!error && isLoading && nodes.length === 0 && (
          <CenteredMessage tone="info" text="Loading C4 view…" />
        )}
        {!error && !isLoading && nodes.length === 0 && (
          <CenteredMessage tone="empty" text="Nothing to show at this level." />
        )}

        <ReactFlow
          nodes={nodes}
          edges={edges}
          nodeTypes={c4NodeTypes}
          edgeTypes={c4EdgeTypes}
          onNodeClick={handleNodeClick}
          onNodeDoubleClick={handleNodeDoubleClick}
          fitView
          fitViewOptions={{ padding: 0.15 }}
          minZoom={0.2}
          maxZoom={2.5}
          proOptions={{ hideAttribution: true }}
          nodesDraggable={false}
          nodesConnectable={false}
        >
          <Background gap={28} size={1} color="rgba(148,163,184,0.18)" />
          <Controls showInteractive={false} />
          <MiniMap pannable zoomable maskColor="rgba(11,18,32,0.85)" />
        </ReactFlow>

        <C4NodeInspector
          data={selectedData}
          onClose={() => setSelectedNodeId(null)}
          {...(selectedData?.kind === "container" ? { onDrillIn: onDrillInto } : {})}
        />
      </div>
    </div>
  );
}

function CenteredMessage({ tone, text }: { tone: "info" | "empty" | "error"; text: string }) {
  const color =
    tone === "error"
      ? "#fca5a5"
      : tone === "empty"
      ? "var(--color-text-tertiary, #64748b)"
      : "var(--color-text-secondary, #94a3b8)";
  return (
    <div
      role="status"
      style={{
        position: "absolute",
        inset: 0,
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        color,
        fontSize: 13,
        pointerEvents: "none",
        zIndex: 3,
      }}
    >
      {text}
    </div>
  );
}
