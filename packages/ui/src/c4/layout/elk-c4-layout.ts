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

import type { ElkNode, ElkExtendedEdge } from "elkjs";
import { runElkLayout } from "./elk-runner";

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

export interface C4LayoutOptions {
  /** Run ELK in a Web Worker when one can start (see `elk-runner.ts`). */
  worker?: boolean;
  /** ELK options merged over the C4 defaults. */
  layoutOptions?: Record<string, string>;
}

/** Nodes go to ELK in input order, which it honours when `considerModelOrder` is set. */
export async function computeC4Layout(
  nodes: readonly C4LayoutNode[],
  edges: readonly C4LayoutEdge[],
  options: C4LayoutOptions = {},
): Promise<Map<string, C4LayoutPosition>> {
  if (nodes.length === 0) return new Map();

  const nodeIds = new Set(nodes.map((n) => n.id));
  const validEdges = edges.filter(
    (e) => nodeIds.has(e.source) && nodeIds.has(e.target) && e.source !== e.target,
  );

  const elkGraph: ElkNode = {
    id: "root",
    layoutOptions: { ...LAYOUT_OPTIONS, ...options.layoutOptions },
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

  const laid = await runElkLayout(elkGraph, { worker: options.worker ?? false });
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
