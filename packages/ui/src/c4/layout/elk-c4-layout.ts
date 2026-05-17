/**
 * ELK layout config tailored for C4 diagrams.
 *
 * C4 reads top-down: people / system at the top, externals below.
 * We use the layered algorithm with `DOWN` direction and a generous
 * inter-layer gap so the labelled relation edges are readable.
 *
 * Pure positioning module — no React Flow imports — so the same layout
 * is reusable for SVG/Mermaid export later.
 */

import ELK from "elkjs/lib/elk.bundled.js";
import type { ElkNode, ElkExtendedEdge } from "elkjs";

export interface C4LayoutNode {
  id: string;
  width: number;
  height: number;
}

export interface C4LayoutEdge {
  id: string;
  source: string;
  target: string;
}

export interface C4LayoutPosition {
  x: number;
  y: number;
  width: number;
  height: number;
}

export const C4_NODE_SIZES = {
  person: { width: 140, height: 90 },
  system: { width: 220, height: 110 },
  external: { width: 180, height: 90 },
  container: { width: 220, height: 110 },
  component: { width: 180, height: 90 },
} as const;

const elk = new ELK();

const LAYOUT_OPTIONS: Record<string, string> = {
  "elk.algorithm": "layered",
  "elk.direction": "DOWN",
  "elk.layered.spacing.nodeNodeBetweenLayers": "90",
  "elk.layered.spacing.edgeNodeBetweenLayers": "40",
  "elk.spacing.nodeNode": "55",
  "elk.spacing.componentComponent": "70",
  "elk.layered.crossingMinimization.strategy": "LAYER_SWEEP",
  "elk.padding": "[top=30,left=30,bottom=30,right=30]",
};

export async function computeC4Layout(
  nodes: C4LayoutNode[],
  edges: C4LayoutEdge[],
): Promise<Map<string, C4LayoutPosition>> {
  if (nodes.length === 0) return new Map();

  const nodeIds = new Set(nodes.map((n) => n.id));
  const validEdges = edges.filter(
    (e) => nodeIds.has(e.source) && nodeIds.has(e.target) && e.source !== e.target,
  );

  const elkGraph: ElkNode = {
    id: "root",
    layoutOptions: LAYOUT_OPTIONS,
    children: nodes.map<ElkNode>((n) => ({
      id: n.id,
      width: n.width,
      height: n.height,
    })),
    edges: validEdges.map<ElkExtendedEdge>((e) => ({
      id: e.id,
      sources: [e.source],
      targets: [e.target],
    })),
  };

  const laid = await elk.layout(elkGraph);
  const positions = new Map<string, C4LayoutPosition>();
  for (const child of laid.children ?? []) {
    positions.set(child.id, {
      x: child.x ?? 0,
      y: child.y ?? 0,
      width: child.width ?? 0,
      height: child.height ?? 0,
    });
  }
  return positions;
}
