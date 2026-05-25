import ELK from "elkjs/lib/elk.bundled.js";
import type { ElkNode, ElkExtendedEdge } from "elkjs";
import { ARCH_NODE_SIZES } from "../../graph-primitives/tone-styles";

export { ARCH_NODE_SIZES };

export interface ContainerAtom {
  id: string;
  label: string;
  childNodeIds: string[];
  estimatedSize?: { width: number; height: number };
}

export interface PortalSpec {
  id: string;
  sourceLayerId: string;
  targetLayerId: string;
  targetLayerName: string;
  edgeCount: number;
}

export interface Stage1Result {
  positions: Map<string, { x: number; y: number; width: number; height: number }>;
  issues: string[];
}

export interface Stage2Result {
  positions: Map<string, { x: number; y: number }>;
  actualSize: { width: number; height: number };
}

const STAGE1_OPTIONS: Record<string, string> = {
  "elk.algorithm": "layered",
  "elk.direction": "DOWN",
  "elk.layered.spacing.nodeNodeBetweenLayers": "80",
  "elk.spacing.nodeNode": "60",
  "elk.layered.crossingMinimization.strategy": "LAYER_SWEEP",
  "elk.padding": "[top=40,left=20,bottom=20,right=20]",
};

const STAGE2_OPTIONS: Record<string, string> = {
  "elk.algorithm": "layered",
  "elk.direction": "DOWN",
  "elk.layered.spacing.nodeNodeBetweenLayers": "60",
  "elk.spacing.nodeNode": "40",
  "elk.padding": "[top=20,left=15,bottom=15,right=15]",
};

const elk = new ELK();

export function repairElkInput(
  nodes: { id: string }[],
  edges: { id: string; source: string; target: string }[],
): { edges: { id: string; source: string; target: string }[]; issues: string[] } {
  const nodeIds = new Set(nodes.map((n) => n.id));
  const issues: string[] = [];
  const seen = new Set<string>();
  const cleaned: { id: string; source: string; target: string }[] = [];

  for (const edge of edges) {
    if (!nodeIds.has(edge.source)) {
      issues.push(`Edge ${edge.id}: source "${edge.source}" not in node set`);
      continue;
    }
    if (!nodeIds.has(edge.target)) {
      issues.push(`Edge ${edge.id}: target "${edge.target}" not in node set`);
      continue;
    }
    if (edge.source === edge.target) {
      issues.push(`Edge ${edge.id}: self-loop on "${edge.source}"`);
      continue;
    }
    const key = `${edge.source}→${edge.target}`;
    if (seen.has(key)) {
      issues.push(`Edge ${edge.id}: duplicate of ${key}`);
      continue;
    }
    seen.add(key);
    cleaned.push(edge);
  }

  return { edges: cleaned, issues };
}

export function estimateContainerSize(childCount: number): { width: number; height: number } {
  const width = Math.min(800, Math.max(200, childCount * 130));
  const height = Math.min(600, Math.max(120, childCount * 55));
  return { width, height };
}

export async function computeStage1Layout(
  containers: ContainerAtom[],
  standaloneNodes: { id: string; width: number; height: number }[],
  portalNodes: PortalSpec[],
  edges: { id: string; source: string; target: string }[],
  containerSizeMemory: Map<string, { width: number; height: number }>,
): Promise<Stage1Result> {
  const allNodes: { id: string; width: number; height: number }[] = [];

  for (const container of containers) {
    const memorized = containerSizeMemory.get(container.id);
    const size = memorized ?? container.estimatedSize ?? estimateContainerSize(container.childNodeIds.length);
    allNodes.push({ id: container.id, width: size.width, height: size.height });
  }

  for (const node of standaloneNodes) {
    allNodes.push(node);
  }

  for (const portal of portalNodes) {
    allNodes.push({ id: portal.id, width: ARCH_NODE_SIZES.portal.width, height: ARCH_NODE_SIZES.portal.height });
  }

  if (allNodes.length === 0) {
    return { positions: new Map(), issues: [] };
  }

  const { edges: repairedEdges, issues } = repairElkInput(allNodes, edges);

  const elkGraph: ElkNode = {
    id: "root",
    layoutOptions: STAGE1_OPTIONS,
    children: allNodes.map<ElkNode>((n) => ({
      id: n.id,
      width: n.width,
      height: n.height,
    })),
    edges: repairedEdges.map<ElkExtendedEdge>((e) => ({
      id: e.id,
      sources: [e.source],
      targets: [e.target],
    })),
  };

  const laid = await elk.layout(elkGraph);
  const positions = new Map<string, { x: number; y: number; width: number; height: number }>();

  for (const child of laid.children ?? []) {
    positions.set(child.id, {
      x: child.x ?? 0,
      y: child.y ?? 0,
      width: child.width ?? 0,
      height: child.height ?? 0,
    });
  }

  return { positions, issues };
}

export async function computeStage2Layout(
  children: { id: string; width: number; height: number }[],
  internalEdges: { id: string; source: string; target: string }[],
): Promise<Stage2Result> {
  if (children.length === 0) {
    return { positions: new Map(), actualSize: { width: 0, height: 0 } };
  }

  const { edges: repairedEdges } = repairElkInput(children, internalEdges);

  const elkGraph: ElkNode = {
    id: "container-root",
    layoutOptions: STAGE2_OPTIONS,
    children: children.map<ElkNode>((n) => ({
      id: n.id,
      width: n.width,
      height: n.height,
    })),
    edges: repairedEdges.map<ElkExtendedEdge>((e) => ({
      id: e.id,
      sources: [e.source],
      targets: [e.target],
    })),
  };

  const laid = await elk.layout(elkGraph);
  const positions = new Map<string, { x: number; y: number }>();

  for (const child of laid.children ?? []) {
    positions.set(child.id, {
      x: child.x ?? 0,
      y: child.y ?? 0,
    });
  }

  const actualSize = {
    width: laid.width ?? 0,
    height: laid.height ?? 0,
  };

  return { positions, actualSize };
}
