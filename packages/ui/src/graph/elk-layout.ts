/**
 * ELK layout engine — computes node positions for the Sigma canvas.
 *
 * Uses elkjs for deterministic hierarchical layout with compound nodes
 * representing directories.
 */

import ELK from "elkjs/lib/elk.bundled.js";
import type { ElkNode, ElkExtendedEdge } from "elkjs";
import type {
  GraphNode as GraphNodeResponse,
  GraphLink as GraphEdgeResponse,
  ModuleNode as ModuleNodeResponse,
  ModuleEdge as ModuleEdgeResponse,
} from "@repowise-dev/types/graph";

// ---- Constants ----

const MODULE_NODE_WIDTH = 140;
const MODULE_NODE_HEIGHT = 36;
const FILE_NODE_WIDTH = 140;
const FILE_NODE_HEIGHT = 36;

const elk = new ELK();

// ---- Types ----

export interface FileNodeData {
  nodeType: "file";
  label: string;
  fullPath: string;
  language: string;
  symbolCount: number;
  pagerank: number;
  betweenness: number;
  communityId: number;
  isTest: boolean;
  isEntryPoint: boolean;
  hasDoc: boolean;
  isHotspot?: boolean | undefined;
  isDead?: boolean | undefined;
  [key: string]: unknown;
}

export interface ModuleNodeData {
  nodeType: "module";
  label: string;
  fullPath: string;
  fileCount: number;
  symbolCount: number;
  avgPagerank: number;
  docCoveragePct: number;
  /** True when this entry represents a single file, not a directory */
  isFile?: boolean;
  /** File-level fields (only set when isFile=true) */
  language?: string;
  hasDoc?: boolean;
  isEntryPoint?: boolean;
  isTest?: boolean;
  dominantCommunityId?: number | undefined;
  /** Health rollups from the module API (0 / false / null when absent). */
  hotspotCount?: number;
  deadCount?: number;
  hasDecision?: boolean;
  primaryOwner?: string | null;
  [key: string]: unknown;
}

export interface DependencyEdgeData {
  importedNames: string[];
  edgeCount: number;
  confidence?: number | undefined;
  [key: string]: unknown;
}

export interface GraphEdge {
  id: string;
  source: string;
  target: string;
  type?: string | undefined;
  data?: Record<string, unknown> | undefined;
}

// ---- Helpers ----

/** Extract directory path from a file node_id (everything before the last /). */
function dirOf(nodeId: string): string {
  const idx = nodeId.lastIndexOf("/");
  return idx > 0 ? nodeId.slice(0, idx) : "";
}

/** Build all ancestor directories for grouping (e.g. "src/auth/middleware" → ["src", "src/auth", "src/auth/middleware"]). */
function ancestorDirs(dir: string): string[] {
  if (!dir) return [];
  const parts = dir.split("/");
  const result: string[] = [];
  for (let i = 0; i < parts.length; i++) {
    result.push(parts.slice(0, i + 1).join("/"));
  }
  return result;
}

/** Deduplicate edges: group by (source, target) pair, merge imported_names, sum count. */
function deduplicateEdges(
  edges: GraphEdgeResponse[],
): { source: string; target: string; importedNames: string[]; edgeCount: number; confidence: number | undefined }[] {
  const map = new Map<
    string,
    { source: string; target: string; importedNames: string[]; edgeCount: number; confidence: number | undefined }
  >();
  for (const e of edges) {
    const key = `${e.source}→${e.target}`;
    const existing = map.get(key);
    if (existing) {
      existing.importedNames.push(...e.imported_names);
      existing.edgeCount++;
      if (e.confidence != null) {
        existing.confidence = Math.max(existing.confidence ?? 0, e.confidence);
      }
    } else {
      map.set(key, {
        source: e.source,
        target: e.target,
        importedNames: [...e.imported_names],
        edgeCount: 1,
        confidence: e.confidence,
      });
    }
  }
  return Array.from(map.values());
}

// ---- Client-side module grouping (for drill-down) ----

/**
 * Groups file-level graph nodes into modules at the next directory level
 * below `prefix`. Mirrors what the backend `/modules` endpoint does but
 * scoped to a sub-tree.
 *
 * @param nodes  All file-level nodes (from the full graph)
 * @param edges  All file-level edges
 * @param prefix The current drill-down prefix, e.g. "packages" or "packages/web".
 *               Pass "" for the top level.
 * @returns Module nodes and inter-module edges, ready for `layoutModuleGraph`.
 */
export function groupNodesAsModules(
  nodes: GraphNodeResponse[],
  edges: GraphEdgeResponse[],
  prefix: string,
): { moduleNodes: ModuleNodeResponse[]; moduleEdges: ModuleEdgeResponse[]; fileEntries: Map<string, GraphNodeResponse> } {
  const slash = prefix ? prefix + "/" : "";

  // Filter nodes under this prefix
  const scopedNodes = prefix
    ? nodes.filter((n) => n.node_id.startsWith(slash))
    : nodes;

  if (scopedNodes.length === 0) {
    return { moduleNodes: [], moduleEdges: [], fileEntries: new Map() };
  }

  // Auto-detect monorepo: if >50% of root-level nodes live under packages/,
  // group by second segment (packages/<name>) instead of first segment.
  const isMonorepo = !prefix &&
    scopedNodes.filter((n) => n.node_id.startsWith("packages/")).length > scopedNodes.length * 0.5;

  // Group by next path segment after the prefix
  const modules = new Map<string, GraphNodeResponse[]>();
  const nodeToModule = new Map<string, string>();

  for (const n of scopedNodes) {
    const rest = prefix ? n.node_id.slice(slash.length) : n.node_id;

    let moduleId: string;
    if (isMonorepo && rest.startsWith("packages/")) {
      const afterPkg = rest.slice("packages/".length);
      const idx = afterPkg.indexOf("/");
      moduleId = idx >= 0 ? "packages/" + afterPkg.slice(0, idx) : rest;
    } else {
      const slashIdx = rest.indexOf("/");
      moduleId = slashIdx >= 0
        ? (prefix ? slash + rest.slice(0, slashIdx) : rest.slice(0, slashIdx))
        : (prefix ? slash + rest : rest);
    }

    const list = modules.get(moduleId) ?? [];
    list.push(n);
    modules.set(moduleId, list);
    nodeToModule.set(n.node_id, moduleId);
  }

  // Build module node responses
  const moduleNodes: ModuleNodeResponse[] = [];
  // Track which entries are single files vs directories
  const fileEntries = new Map<string, GraphNodeResponse>();
  for (const [moduleId, groupedNodes] of modules) {
    const fileCount = groupedNodes.length;
    const symbolCount = groupedNodes.reduce((s, n) => s + n.symbol_count, 0);
    const avgPagerank =
      groupedNodes.reduce((s, n) => s + n.pagerank, 0) / Math.max(fileCount, 1);
    const docCount = groupedNodes.filter((n) => n.has_doc).length;
    const docCoveragePct = docCount / Math.max(fileCount, 1);

    // A "file entry" is when the module_id equals a single node's full path
    // (i.e., it's a direct file at this level, not a subdirectory)
    const firstNode = groupedNodes[0];
    const isSingleFile = fileCount === 1 && firstNode?.node_id === moduleId;
    if (isSingleFile && firstNode) {
      fileEntries.set(moduleId, firstNode);
    }

    moduleNodes.push({
      module_id: moduleId,
      file_count: fileCount,
      symbol_count: symbolCount,
      avg_pagerank: avgPagerank,
      doc_coverage_pct: docCoveragePct,
    });
  }

  // Build inter-module edges
  const scopedNodeSet = new Set(scopedNodes.map((n) => n.node_id));
  const edgeCounts = new Map<string, number>();
  for (const e of edges) {
    if (!scopedNodeSet.has(e.source) || !scopedNodeSet.has(e.target)) continue;
    const srcModule = nodeToModule.get(e.source);
    const tgtModule = nodeToModule.get(e.target);
    if (srcModule && tgtModule && srcModule !== tgtModule) {
      const key = `${srcModule}→${tgtModule}`;
      edgeCounts.set(key, (edgeCounts.get(key) ?? 0) + 1);
    }
  }

  const moduleEdges: ModuleEdgeResponse[] = Array.from(edgeCounts.entries()).map(
    ([key, count]) => {
      const [source = "", target = ""] = key.split("→");
      return { source, target, edge_count: count };
    },
  );

  return { moduleNodes, moduleEdges, fileEntries };
}

// ---- Pure position computation (for Sigma bridge) ----

export interface ElkPositionResult {
  positions: Map<string, { x: number; y: number; width: number; height: number }>;
  groups: Map<string, { x: number; y: number; width: number; height: number }>;
}

function flattenPositions(
  elkNode: ElkNode,
  offsetX = 0,
  offsetY = 0,
  positions: Map<string, { x: number; y: number; width: number; height: number }>,
  groups: Map<string, { x: number; y: number; width: number; height: number }>,
) {
  if (!elkNode.children) return;
  for (const child of elkNode.children) {
    const x = (child.x ?? 0) + offsetX;
    const y = (child.y ?? 0) + offsetY;
    const w = child.width ?? 0;
    const h = child.height ?? 0;
    if (child.id.startsWith("dir:")) {
      groups.set(child.id, { x, y, width: w, height: h });
      flattenPositions(child, x, y, positions, groups);
    } else {
      positions.set(child.id, { x, y, width: w, height: h });
    }
  }
}

export async function computeElkFilePositions(
  nodes: GraphNodeResponse[],
  edges: GraphEdgeResponse[],
): Promise<ElkPositionResult> {
  if (nodes.length === 0) return { positions: new Map(), groups: new Map() };

  const nodeSet = new Set(nodes.map((n) => n.node_id));
  const validEdges = edges.filter((e) => nodeSet.has(e.source) && nodeSet.has(e.target));
  const dedupedEdges = deduplicateEdges(validEdges);

  const dirSet = new Set<string>();
  for (const n of nodes) {
    const dir = dirOf(n.node_id);
    if (dir) {
      for (const ancestor of ancestorDirs(dir)) {
        dirSet.add(ancestor);
      }
    }
  }

  const dirs = Array.from(dirSet).sort();

  const elkGraph: ElkNode = {
    id: "root",
    layoutOptions: {
      "elk.algorithm": "layered",
      "elk.direction": "DOWN",
      "elk.layered.spacing.nodeNodeBetweenLayers": "80",
      "elk.layered.spacing.edgeNodeBetweenLayers": "35",
      "elk.spacing.nodeNode": "40",
      "elk.spacing.componentComponent": "60",
      "elk.hierarchyHandling": "INCLUDE_CHILDREN",
      "elk.layered.crossingMinimization.strategy": "LAYER_SWEEP",
      "elk.padding": "[top=45,left=15,bottom=15,right=15]",
    },
    children: [],
    edges: [],
  };

  const dirElkNodes = new Map<string, ElkNode>();
  for (const dir of dirs) {
    const parts = dir.split("/");
    const label = parts[parts.length - 1] ?? dir;
    const elkNode: ElkNode = {
      id: `dir:${dir}`,
      labels: [{ text: label }],
      layoutOptions: {
        "elk.padding": "[top=40,left=12,bottom=12,right=12]",
      },
      children: [],
    };
    dirElkNodes.set(dir, elkNode);
  }

  for (const dir of dirs) {
    const parentDir = dirOf(dir);
    const parentElk = parentDir ? dirElkNodes.get(parentDir) : null;
    const elkNode = dirElkNodes.get(dir)!;
    if (parentElk) {
      parentElk.children!.push(elkNode);
    } else {
      elkGraph.children!.push(elkNode);
    }
  }

  for (const n of nodes) {
    const scale = 1 + Math.min(0.6, n.pagerank * 25);
    const elkFileNode: ElkNode = {
      id: n.node_id,
      width: Math.round(FILE_NODE_WIDTH * scale),
      height: Math.round(FILE_NODE_HEIGHT * scale),
      labels: [{ text: n.node_id.split("/").pop() ?? n.node_id }],
    };
    const dir = dirOf(n.node_id);
    const parentElk = dir ? dirElkNodes.get(dir) : null;
    if (parentElk) {
      parentElk.children!.push(elkFileNode);
    } else {
      elkGraph.children!.push(elkFileNode);
    }
  }

  const elkEdges: ElkExtendedEdge[] = dedupedEdges.map((e, i) => ({
    id: `e${i}`,
    sources: [e.source],
    targets: [e.target],
  }));
  elkGraph.edges = elkEdges;

  const layout = await elk.layout(elkGraph);

  const positions = new Map<string, { x: number; y: number; width: number; height: number }>();
  const groups = new Map<string, { x: number; y: number; width: number; height: number }>();
  flattenPositions(layout, 0, 0, positions, groups);

  return { positions, groups };
}

export async function computeElkModulePositions(
  moduleNodes: ModuleNodeResponse[],
  moduleEdges: ModuleEdgeResponse[],
): Promise<Map<string, { x: number; y: number }>> {
  if (moduleNodes.length === 0) return new Map();

  // Modules without any dependency edge would each become their own ELK
  // component, packed side by side into one huge band that drowns out the
  // actual layering. Run ELK on the connected subgraph only; the isolated
  // modules get a compact grid below it (see after extractPositions).
  const connectedIds = new Set<string>();
  for (const e of moduleEdges) {
    connectedIds.add(e.source);
    connectedIds.add(e.target);
  }
  const isolated = moduleNodes.filter((m) => !connectedIds.has(m.module_id));
  const layoutNodes =
    isolated.length === moduleNodes.length
      ? moduleNodes
      : moduleNodes.filter((m) => connectedIds.has(m.module_id));

  const moduleIds = new Set(layoutNodes.map((m) => m.module_id));

  // Build all ancestor directories for compound node nesting
  const dirSet = new Set<string>();
  for (const m of layoutNodes) {
    const ancestors = ancestorDirs(dirOf(m.module_id));
    for (const a of ancestors) {
      if (!moduleIds.has(a)) dirSet.add(a);
    }
  }
  const dirs = Array.from(dirSet).sort();

  // Create ELK compound nodes for each directory
  const dirElkNodes = new Map<string, ElkNode>();
  for (const dir of dirs) {
    dirElkNodes.set(dir, {
      id: `dir:${dir}`,
      labels: [{ text: dir.split("/").pop() ?? dir }],
      layoutOptions: {
        "elk.padding": "[top=35,left=12,bottom=12,right=12]",
      },
      children: [],
    });
  }

  // Nest directories into their parents
  const rootChildren: ElkNode[] = [];
  for (const dir of dirs) {
    const parentDir = dirOf(dir);
    const parentElk = parentDir ? dirElkNodes.get(parentDir) : null;
    const elkNode = dirElkNodes.get(dir)!;
    if (parentElk) {
      parentElk.children!.push(elkNode);
    } else {
      rootChildren.push(elkNode);
    }
  }

  // Place module nodes into their parent directory (or root)
  for (const m of layoutNodes) {
    const elkNode: ElkNode = {
      id: m.module_id,
      width: MODULE_NODE_WIDTH,
      height: MODULE_NODE_HEIGHT,
      labels: [{ text: m.module_id.split("/").pop() ?? m.module_id }],
    };
    const parentDir = dirOf(m.module_id);
    const parentElk = parentDir ? dirElkNodes.get(parentDir) : null;
    if (parentElk) {
      parentElk.children!.push(elkNode);
    } else {
      rootChildren.push(elkNode);
    }
  }

  // Remove empty compound nodes (directories with no children)
  function pruneEmpty(node: ElkNode): boolean {
    if (!node.children) return true;
    node.children = node.children.filter(pruneEmpty);
    return node.children.length > 0 || !node.id.startsWith("dir:");
  }
  rootChildren.forEach(pruneEmpty);

  const elkGraph: ElkNode = {
    id: "root",
    layoutOptions: {
      "elk.algorithm": "layered",
      "elk.direction": "DOWN",
      "elk.layered.spacing.nodeNodeBetweenLayers": "60",
      "elk.layered.spacing.edgeNodeBetweenLayers": "30",
      "elk.spacing.nodeNode": "25",
      "elk.spacing.componentComponent": "50",
      "elk.hierarchyHandling": "INCLUDE_CHILDREN",
      "elk.layered.crossingMinimization.strategy": "LAYER_SWEEP",
      "elk.padding": "[top=25,left=20,bottom=20,right=20]",
    },
    children: rootChildren,
    edges: moduleEdges.map((e, i) => ({
      id: `me${i}`,
      sources: [e.source],
      targets: [e.target],
    })),
  };

  const layout = await elk.layout(elkGraph);

  const positions = new Map<string, { x: number; y: number }>();
  const extractPositions = (node: ElkNode, ox = 0, oy = 0) => {
    for (const child of node.children ?? []) {
      const x = (child.x ?? 0) + ox;
      const y = (child.y ?? 0) + oy;
      if (child.id.startsWith("dir:")) {
        extractPositions(child, x, y);
      } else {
        positions.set(child.id, { x, y });
      }
    }
  };
  extractPositions(layout);

  // Shelve isolated modules in a grid below the layered area so they stay
  // visible without stretching the layers into a single band.
  if (isolated.length > 0 && isolated.length < moduleNodes.length) {
    let minX = Infinity;
    let maxX = -Infinity;
    let maxY = -Infinity;
    for (const p of positions.values()) {
      minX = Math.min(minX, p.x);
      maxX = Math.max(maxX, p.x);
      maxY = Math.max(maxY, p.y);
    }
    if (!Number.isFinite(minX)) {
      minX = 0;
      maxX = 0;
      maxY = 0;
    }
    // Wider than the layered spacing: grid rows have no edges to separate
    // nodes visually, so labels need the room instead.
    const cellW = MODULE_NODE_WIDTH + 90;
    const cellH = MODULE_NODE_HEIGHT + 45;
    const cols = Math.max(
      Math.ceil(Math.sqrt(isolated.length)),
      Math.min(isolated.length, Math.floor((maxX - minX) / cellW) || 1),
    );
    const startY = maxY + MODULE_NODE_HEIGHT + 90;
    isolated.forEach((m, i) => {
      positions.set(m.module_id, {
        x: minX + (i % cols) * cellW,
        y: startY + Math.floor(i / cols) * cellH,
      });
    });
  }

  return positions;
}

