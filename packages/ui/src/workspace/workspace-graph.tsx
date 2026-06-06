"use client";

import { useMemo, useCallback } from "react";
import {
  ReactFlow,
  Controls,
  Background,
  BackgroundVariant,
  ReactFlowProvider,
  type Node,
  type Edge,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import {
  forceSimulation,
  forceManyBody,
  forceLink,
  forceCollide,
  forceCenter,
} from "d3-force";
import { WorkspaceGraphNode, type WorkspaceGraphNodeData } from "./workspace-graph-node";

export interface WorkspaceGraphData {
  nodes: Array<{
    repo_id: string;
    name: string;
    file_count: number;
    coverage_pct: number;
    health_score: number;
    top_language: string;
  }>;
  edges: Array<{
    source: string;
    target: string;
    type: "contract" | "co_change";
    strength: number;
    label: string | null;
  }>;
}

export interface WorkspaceGraphProps {
  data: WorkspaceGraphData;
  onRepoClick?: (repoId: string) => void;
}

const nodeTypes = { workspaceRepo: WorkspaceGraphNode };

interface SimNode {
  id: string;
  x?: number;
  y?: number;
  vx?: number;
  vy?: number;
}

function computeLayout(data: WorkspaceGraphData): { nodes: Node[]; edges: Edge[] } {
  if (data.nodes.length === 0) return { nodes: [], edges: [] };

  const nodeSet = new Set(data.nodes.map((n) => n.repo_id));
  const simNodes: SimNode[] = data.nodes.map((n) => ({ id: n.repo_id }));
  const simLinks = data.edges
    .filter((e) => nodeSet.has(e.source) && nodeSet.has(e.target))
    .map((e) => ({ source: e.source, target: e.target }));

  /* eslint-disable @typescript-eslint/no-explicit-any */
  const sim = forceSimulation(simNodes as any[])
    .force("charge", forceManyBody().strength(-400))
    .force(
      "link",
      forceLink(simLinks as any[])
        .id((d: any) => d.id)
        .distance(200),
    )
    .force("collide", forceCollide(100))
    .force("center", forceCenter(0, 0))
    .stop();
  /* eslint-enable @typescript-eslint/no-explicit-any */

  for (let i = 0; i < 100; i++) sim.tick();

  const rfNodes: Node[] = simNodes.map((sn) => {
    const apiNode = data.nodes.find((n) => n.repo_id === sn.id)!;
    return {
      id: sn.id,
      type: "workspaceRepo",
      position: { x: sn.x ?? 0, y: sn.y ?? 0 },
      data: {
        repoId: apiNode.repo_id,
        name: apiNode.name,
        fileCount: apiNode.file_count,
        coveragePct: apiNode.coverage_pct,
        healthScore: apiNode.health_score,
        topLanguage: apiNode.top_language,
      } satisfies WorkspaceGraphNodeData,
    };
  });

  const rfEdges: Edge[] = data.edges
    .filter((e) => nodeSet.has(e.source) && nodeSet.has(e.target))
    .map((e, i) => ({
      id: `ws-edge-${i}`,
      source: e.source,
      target: e.target,
      label: e.label ?? undefined,
      style: {
        strokeWidth: 1 + e.strength * 4,
        stroke: e.type === "contract" ? "var(--color-accent-secondary)" : "var(--color-edge-co-change)",
        strokeDasharray: e.type === "co_change" ? "6 3" : undefined,
      },
      labelStyle: { fontSize: 10, fill: "var(--color-text-tertiary)" },
    }));

  return { nodes: rfNodes, edges: rfEdges };
}

function WorkspaceGraphInner({ data, onRepoClick }: WorkspaceGraphProps) {
  const { nodes, edges } = useMemo(() => computeLayout(data), [data]);

  const handleNodeClick = useCallback(
    (_: React.MouseEvent, node: Node) => {
      const d = node.data as unknown as WorkspaceGraphNodeData;
      onRepoClick?.(d.repoId);
    },
    [onRepoClick],
  );

  return (
    <ReactFlow
      nodes={nodes}
      edges={edges}
      nodeTypes={nodeTypes}
      onNodeClick={handleNodeClick}
      fitView
      fitViewOptions={{ padding: 0.3, maxZoom: 1.2 }}
      minZoom={0.3}
      maxZoom={2}
      proOptions={{ hideAttribution: true }}
      nodesDraggable
      className="!bg-transparent"
    >
      <Background variant={BackgroundVariant.Dots} gap={20} size={0.5} color="rgba(255,255,255,0.03)" />
      <Controls
        showInteractive={false}
        className="!border-[var(--color-border-default)] !bg-[var(--color-bg-elevated)] !shadow-lg [&>button]:!border-[var(--color-border-default)] [&>button]:!bg-[var(--color-bg-elevated)] [&>button]:!text-[var(--color-text-secondary)] [&>button:hover]:!bg-[var(--color-bg-overlay)] [&>button:hover]:!text-[var(--color-text-primary)]"
      />
    </ReactFlow>
  );
}

export function WorkspaceGraph(props: WorkspaceGraphProps) {
  return (
    <ReactFlowProvider>
      <WorkspaceGraphInner {...props} />
    </ReactFlowProvider>
  );
}
