"use client";

/**
 * Self-contained decision-graph diagram.
 *
 * Renders decision -> decision typed edges (supersedes / refines / relates_to /
 * conflicts_with) plus decision -> code "governs" links, laid out with the same
 * ELK layered engine the C4 view uses (`computeC4Layout`). Conflicts are
 * highlighted in red. Clicking a decision node calls `onSelectDecision`.
 *
 * Props-driven: the host fetches the `DecisionGraph` (via SWR) and passes it in.
 */

import { memo, useEffect, useMemo, useState } from "react";
import {
  Background,
  BaseEdge,
  Controls,
  EdgeLabelRenderer,
  Handle,
  Position,
  ReactFlow,
  ReactFlowProvider,
  getBezierPath,
  type Edge,
  type EdgeProps,
  type Node,
  type NodeMouseHandler,
  type NodeProps,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { computeC4Layout, type C4LayoutNode } from "../c4/layout/elk-c4-layout";
import type {
  DecisionEdgeKind,
  DecisionGraph,
  DecisionGraphNode,
} from "@repowise-dev/types/decisions";

const DECISION_NODE_SIZE = { width: 200, height: 72 };
const CODE_NODE_SIZE = { width: 180, height: 44 };

const STATUS_DOT: Record<string, string> = {
  active: "var(--color-success)",
  proposed: "var(--color-info)",
  deprecated: "var(--color-error)",
  superseded: "var(--color-text-tertiary)",
};

const EDGE_STYLE: Record<DecisionEdgeKind, { stroke: string; label: string; dashed?: boolean }> = {
  supersedes: { stroke: "var(--color-accent-secondary)", label: "supersedes" },
  refines: { stroke: "var(--color-edge-co-change)", label: "refines" },
  relates_to: { stroke: "var(--color-text-tertiary)", label: "relates to" },
  conflicts_with: { stroke: "var(--color-error)", label: "conflicts" },
};

// ---------------------------------------------------------------------------
// Node + edge data carried through React Flow
// ---------------------------------------------------------------------------

interface DecisionNodeData extends Record<string, unknown> {
  kind: "decision";
  decision: DecisionGraphNode;
}
interface CodeNodeData extends Record<string, unknown> {
  kind: "code";
  label: string;
  linkType: "file" | "module";
}
interface DecisionEdgeData extends Record<string, unknown> {
  edgeKind?: DecisionEdgeKind;
  governs?: boolean;
}

// ---------------------------------------------------------------------------
// Node components
// ---------------------------------------------------------------------------

const DecisionNode = memo(function DecisionNode({ data }: NodeProps) {
  const { decision } = data as DecisionNodeData;
  const dot = STATUS_DOT[decision.status] ?? STATUS_DOT.active;
  const stale = decision.staleness_score > 0.5;
  return (
    <div
      style={{
        width: DECISION_NODE_SIZE.width,
        minHeight: DECISION_NODE_SIZE.height,
        padding: "8px 10px",
        borderRadius: 8,
        border: `1px solid ${stale ? "var(--color-warning)" : "var(--color-border-default)"}`,
        background: "var(--color-bg-surface)",
        color: "var(--color-text-primary)",
        fontSize: 12,
        cursor: "pointer",
      }}
      title={decision.title}
    >
      <Handle type="target" position={Position.Top} style={{ opacity: 0 }} />
      <div style={{ display: "flex", alignItems: "center", gap: 6, marginBottom: 4 }}>
        <span style={{ width: 9, height: 9, borderRadius: 999, background: dot, flexShrink: 0 }} />
        <span style={{ fontSize: 10, textTransform: "capitalize", color: "var(--color-text-tertiary)" }}>
          {decision.status}
          {stale && " · stale"}
        </span>
      </div>
      <div
        style={{
          fontWeight: 500,
          lineHeight: 1.25,
          display: "-webkit-box",
          WebkitLineClamp: 2,
          WebkitBoxOrient: "vertical",
          overflow: "hidden",
        }}
      >
        {decision.title}
      </div>
      <Handle type="source" position={Position.Bottom} style={{ opacity: 0 }} />
    </div>
  );
});

const CodeNode = memo(function CodeNode({ data }: NodeProps) {
  const { label, linkType } = data as CodeNodeData;
  return (
    <div
      style={{
        width: CODE_NODE_SIZE.width,
        minHeight: CODE_NODE_SIZE.height,
        padding: "6px 8px",
        borderRadius: 6,
        border: "1px dashed var(--color-border-default)",
        background: "var(--color-bg-elevated)",
        color: "var(--color-text-secondary)",
        fontSize: 10,
        fontFamily: "var(--font-mono, ui-monospace, monospace)",
      }}
      title={label}
    >
      <Handle type="target" position={Position.Top} style={{ opacity: 0 }} />
      <div style={{ fontSize: 8, textTransform: "uppercase", opacity: 0.7 }}>{linkType}</div>
      <div style={{ wordBreak: "break-all", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
        {label}
      </div>
    </div>
  );
});

const nodeTypes = { decision: DecisionNode, code: CodeNode };

// ---------------------------------------------------------------------------
// Edge component
// ---------------------------------------------------------------------------

const DecisionEdge = memo(function DecisionEdge(props: EdgeProps) {
  const { id, sourceX, sourceY, targetX, targetY, sourcePosition, targetPosition, data } = props;
  const ed = data as DecisionEdgeData | undefined;
  const [path, labelX, labelY] = getBezierPath({
    sourceX,
    sourceY,
    sourcePosition,
    targetX,
    targetY,
    targetPosition,
  });

  if (ed?.governs) {
    return (
      <BaseEdge
        id={id}
        path={path}
        style={{ stroke: "var(--color-text-tertiary)", strokeWidth: 1, strokeDasharray: "4 3" }}
      />
    );
  }

  const kind = ed?.edgeKind ?? "relates_to";
  const style = EDGE_STYLE[kind];
  const isConflict = kind === "conflicts_with";

  return (
    <>
      <BaseEdge
        id={id}
        path={path}
        style={{
          stroke: style.stroke,
          strokeWidth: isConflict ? 2.5 : 1.5,
          strokeDasharray: kind === "relates_to" ? "5 4" : undefined,
        }}
      />
      <EdgeLabelRenderer>
        <div
          style={{
            position: "absolute",
            transform: `translate(-50%, -50%) translate(${labelX}px, ${labelY}px)`,
            background: isConflict ? "var(--color-error)" : "var(--color-bg-overlay)",
            color: isConflict ? "white" : "var(--color-text-primary)",
            padding: "1px 6px",
            borderRadius: 4,
            fontSize: 11,
            fontWeight: 600,
            pointerEvents: "none",
            border: `1px solid ${isConflict ? "var(--color-error)" : "var(--color-border-default)"}`,
            whiteSpace: "nowrap",
          }}
          className="nodrag nopan"
        >
          {style.label}
        </div>
      </EdgeLabelRenderer>
    </>
  );
});

const edgeTypes = { decision: DecisionEdge };

// ---------------------------------------------------------------------------
// Layout hook
// ---------------------------------------------------------------------------

interface LayoutResult {
  nodes: Node[];
  edges: Edge[];
  loading: boolean;
}

function useDecisionGraphLayout(graph: DecisionGraph | undefined, showCode: boolean): LayoutResult {
  const [result, setResult] = useState<LayoutResult>({ nodes: [], edges: [], loading: false });

  // Stable identity key so the effect only re-runs when content changes.
  const graphKey = useMemo(() => {
    if (!graph) return "";
    return JSON.stringify({
      n: graph.nodes.map((n) => n.id),
      de: graph.decision_edges.map((e) => [e.src, e.dst, e.kind]),
      ce: graph.code_edges.map((e) => [e.decision_id, e.node_id]),
      showCode,
    });
  }, [graph, showCode]);

  useEffect(() => {
    if (!graph || graph.nodes.length === 0) {
      setResult({ nodes: [], edges: [], loading: false });
      return;
    }
    let cancelled = false;
    setResult((r) => ({ ...r, loading: true }));

    const decisionIds = new Set(graph.nodes.map((n) => n.id));

    // Unique code node ids referenced by code edges.
    const codeEdges = showCode ? graph.code_edges : [];
    const codeNodeIds = new Map<string, "file" | "module">();
    for (const e of codeEdges) {
      if (decisionIds.has(e.decision_id)) codeNodeIds.set(e.node_id, e.link_type);
    }

    const layoutNodes: C4LayoutNode[] = [
      ...graph.nodes.map((n) => ({ id: n.id, ...DECISION_NODE_SIZE })),
      ...[...codeNodeIds.keys()].map((id) => ({ id: `code:${id}`, ...CODE_NODE_SIZE })),
    ];

    const decisionEdges = graph.decision_edges.filter(
      (e) => decisionIds.has(e.src) && decisionIds.has(e.dst),
    );
    const layoutEdges = [
      ...decisionEdges.map((e, i) => ({ id: `d${i}:${e.src}->${e.dst}`, source: e.src, target: e.dst })),
      ...codeEdges
        .filter((e) => decisionIds.has(e.decision_id) && codeNodeIds.has(e.node_id))
        .map((e, i) => ({ id: `c${i}:${e.decision_id}->${e.node_id}`, source: e.decision_id, target: `code:${e.node_id}` })),
    ];

    void computeC4Layout(layoutNodes, layoutEdges).then((positions) => {
      if (cancelled) return;
      const nodes: Node[] = [
        ...graph.nodes.map((n) => {
          const pos = positions.get(n.id) ?? { x: 0, y: 0 };
          return {
            id: n.id,
            type: "decision",
            position: { x: pos.x, y: pos.y },
            data: { kind: "decision", decision: n } satisfies DecisionNodeData,
            ...DECISION_NODE_SIZE,
          } as Node;
        }),
        ...[...codeNodeIds.entries()].map(([id, linkType]) => {
          const pos = positions.get(`code:${id}`) ?? { x: 0, y: 0 };
          return {
            id: `code:${id}`,
            type: "code",
            position: { x: pos.x, y: pos.y },
            data: { kind: "code", label: id, linkType } satisfies CodeNodeData,
            ...CODE_NODE_SIZE,
          } as Node;
        }),
      ];

      const edges: Edge[] = [
        ...decisionEdges.map((e, i) => ({
          id: `d${i}:${e.src}->${e.dst}`,
          source: e.src,
          target: e.dst,
          type: "decision",
          data: { edgeKind: e.kind } satisfies DecisionEdgeData,
        })),
        ...codeEdges
          .filter((e) => decisionIds.has(e.decision_id) && codeNodeIds.has(e.node_id))
          .map((e, i) => ({
            id: `c${i}:${e.decision_id}->${e.node_id}`,
            source: e.decision_id,
            target: `code:${e.node_id}`,
            type: "decision",
            data: { governs: true } satisfies DecisionEdgeData,
          })),
      ];

      setResult({ nodes, edges, loading: false });
    });

    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [graphKey]);

  return result;
}

// ---------------------------------------------------------------------------
// Public component
// ---------------------------------------------------------------------------

export interface DecisionGraphViewProps {
  graph: DecisionGraph | undefined;
  isLoading?: boolean;
  onSelectDecision?: (id: string) => void;
}

export function DecisionGraphView(props: DecisionGraphViewProps) {
  return (
    <ReactFlowProvider>
      <DecisionGraphViewInner {...props} />
    </ReactFlowProvider>
  );
}

function DecisionGraphViewInner({ graph, isLoading, onSelectDecision }: DecisionGraphViewProps) {
  const [showCode, setShowCode] = useState(true);
  const { nodes, edges, loading } = useDecisionGraphLayout(graph, showCode);

  const handleNodeClick: NodeMouseHandler = (_, node) => {
    if (node.type === "decision") onSelectDecision?.(node.id);
  };

  const busy = isLoading || loading;
  const empty = !busy && nodes.length === 0;

  return (
    <div
      style={{
        position: "relative",
        width: "100%",
        height: "100%",
        minHeight: 420,
        display: "flex",
        flexDirection: "column",
        background: "var(--color-bg-canvas)",
        border: "1px solid var(--color-border-default)",
        borderRadius: 8,
        overflow: "hidden",
      }}
    >
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 12,
          padding: "6px 10px",
          borderBottom: "1px solid var(--color-border-default)",
          fontSize: 11,
          color: "var(--color-text-secondary)",
        }}
      >
        <Legend color={EDGE_STYLE.supersedes.stroke} label="supersedes" />
        <Legend color={EDGE_STYLE.refines.stroke} label="refines" />
        <Legend color={EDGE_STYLE.relates_to.stroke} label="relates to" />
        <Legend color={EDGE_STYLE.conflicts_with.stroke} label="conflicts" />
        <label style={{ marginLeft: "auto", display: "inline-flex", alignItems: "center", gap: 6, cursor: "pointer" }}>
          <input type="checkbox" checked={showCode} onChange={(e) => setShowCode(e.target.checked)} />
          Code links
        </label>
      </div>

      <div style={{ position: "relative", flex: 1, minHeight: 0 }}>
        {busy && nodes.length === 0 && <Centered text="Laying out decision graph…" />}
        {empty && <Centered text="No decision relationships to show." />}
        <ReactFlow
          nodes={nodes}
          edges={edges}
          nodeTypes={nodeTypes}
          edgeTypes={edgeTypes}
          onNodeClick={handleNodeClick}
          fitView
          fitViewOptions={{ padding: 0.18 }}
          minZoom={0.2}
          maxZoom={2.5}
          proOptions={{ hideAttribution: true }}
          nodesDraggable={false}
          nodesConnectable={false}
        >
          {/* Theme-neutral dot grid — React Flow renders this as an SVG fill
              attribute, which can't resolve var(); a low-alpha gray reads on
              both the warm-paper and charcoal canvases. */}
          <Background gap={28} size={1} color="rgba(140,127,136,0.18)" />
          <Controls showInteractive={false} />
        </ReactFlow>
      </div>
    </div>
  );
}

function Legend({ color, label }: { color: string; label: string }) {
  return (
    <span style={{ display: "inline-flex", alignItems: "center", gap: 4 }}>
      <span style={{ width: 14, height: 2, background: color, display: "inline-block" }} />
      {label}
    </span>
  );
}

function Centered({ text }: { text: string }) {
  return (
    <div
      role="status"
      style={{
        position: "absolute",
        inset: 0,
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        color: "var(--color-text-tertiary)",
        fontSize: 13,
        pointerEvents: "none",
        zIndex: 3,
      }}
    >
      {text}
    </div>
  );
}
