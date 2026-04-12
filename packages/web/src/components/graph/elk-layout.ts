/**
 * ELK layout engine — transforms graph API data into React Flow nodes/edges.
 *
 * Uses elkjs for deterministic hierarchical layout with compound nodes
 * representing directories.
 */

import ELK from "elkjs/lib/elk.bundled.js";
import type { ElkNode, ElkExtendedEdge } from "elkjs";
import type { Node, Edge } from "@xyflow/react";
import type {
  GraphNodeResponse,
  GraphEdgeResponse,
  ModuleNodeResponse,
  ModuleEdgeResponse,
} from "@/lib/api/types";

// ---- Constants ----

const MODULE_NODE_WIDTH = 140;
const MODULE_NODE_HEIGHT = 36;
const FILE_NODE_WIDTH = 140;
const FILE_NODE_HEIGHT = 36;

const elk = new ELK();

// ---- Types ----

export interface FileNodeData {
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
  [key: string]: unknown;
}

export interface ModuleNodeData {
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
  [key: string]: unknown;
}

export interface DependencyEdgeData {
  importedNames: string[];
  edgeCount: number;
  [key: string]: unknown;
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
): { source: string; target: string; importedNames: string[]; edgeCount: number }[] {
  const map = new Map<
    string,
    { source: string; target: string; importedNames: string[]; edgeCount: number }
  >();
  for (const e of edges) {
    const key = `${e.source}→${e.target}`;
    const existing = map.get(key);
    if (existing) {
      existing.importedNames.push(...e.imported_names);
      existing.edgeCount++;
    } else {
      map.set(key, {
        source: e.source,
        target: e.target,
        importedNames: [...e.imported_names],
        edgeCount: 1,
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

  // Group by next path segment after the prefix
  const modules = new Map<string, GraphNodeResponse[]>();
  const nodeToModule = new Map<string, string>();

  for (const n of scopedNodes) {
    const rest = prefix ? n.node_id.slice(slash.length) : n.node_id;
    const slashIdx = rest.indexOf("/");
    const moduleId = slashIdx >= 0
      ? (prefix ? slash + rest.slice(0, slashIdx) : rest.slice(0, slashIdx))
      : (prefix ? slash + rest : rest); // file at this level

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
    const isSingleFile = fileCount === 1 && groupedNodes[0].node_id === moduleId;
    if (isSingleFile) {
      fileEntries.set(moduleId, groupedNodes[0]);
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
      const [source, target] = key.split("→");
      return { source, target, edge_count: count };
    },
  );

  return { moduleNodes, moduleEdges, fileEntries };
}

// ---- Layout for file-level graph ----

export async function layoutFileGraph(
  nodes: GraphNodeResponse[],
  edges: GraphEdgeResponse[],
): Promise<{ nodes: Node[]; edges: Edge[] }> {
  if (nodes.length === 0) return { nodes: [], edges: [] };

  const nodeSet = new Set(nodes.map((n) => n.node_id));
  const validEdges = edges.filter((e) => nodeSet.has(e.source) && nodeSet.has(e.target));
  const dedupedEdges = deduplicateEdges(validEdges);

  // Build directory hierarchy
  const dirSet = new Set<string>();
  for (const n of nodes) {
    const dir = dirOf(n.node_id);
    if (dir) {
      for (const ancestor of ancestorDirs(dir)) {
        dirSet.add(ancestor);
      }
    }
  }

  // Sort dirs so parents come before children
  const dirs = Array.from(dirSet).sort();

  // Build ELK compound graph
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

  // Create dir nodes as groups
  const dirElkNodes = new Map<string, ElkNode>();
  for (const dir of dirs) {
    const parts = dir.split("/");
    const label = parts[parts.length - 1];
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

  // Nest dir nodes into their parents
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

  // Add file nodes (sized by pagerank importance)
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

  // Add edges — ELK needs hierarchical edge format
  const elkEdges: ElkExtendedEdge[] = dedupedEdges.map((e, i) => ({
    id: `e${i}`,
    sources: [e.source],
    targets: [e.target],
  }));
  elkGraph.edges = elkEdges;

  // Compute layout
  const layout = await elk.layout(elkGraph);

  // Flatten layout into React Flow nodes
  const rfNodes: Node[] = [];
  const rfEdges: Edge[] = [];

  function extractNodes(
    elkNode: ElkNode,
    parentId?: string,
    offsetX = 0,
    offsetY = 0,
  ) {
    if (!elkNode.children) return;
    for (const child of elkNode.children) {
      const x = (child.x ?? 0) + offsetX;
      const y = (child.y ?? 0) + offsetY;
      const isDir = child.id.startsWith("dir:");

      if (isDir) {
        const dirPath = child.id.slice(4);
        const label = child.labels?.[0]?.text ?? dirPath;
        rfNodes.push({
          id: child.id,
          type: "moduleGroup",
          position: { x: child.x ?? 0, y: child.y ?? 0 },
          ...(parentId ? { parentId, extent: "parent" as const } : {}),
          style: {
            width: child.width,
            height: child.height,
          },
          data: {
            label,
            fullPath: dirPath,
            fileCount: child.children?.filter((c) => !c.id.startsWith("dir:")).length ?? 0,
          } satisfies Record<string, unknown>,
        });
        // Recurse into children
        extractNodes(child, child.id);
      } else {
        const apiNode = nodes.find((n) => n.node_id === child.id);
        if (apiNode) {
          rfNodes.push({
            id: child.id,
            type: "fileNode",
            position: { x: child.x ?? 0, y: child.y ?? 0 },
            ...(parentId ? { parentId, extent: "parent" as const } : {}),
            data: {
              label: child.labels?.[0]?.text ?? apiNode.node_id,
              fullPath: apiNode.node_id,
              language: apiNode.language,
              symbolCount: apiNode.symbol_count,
              pagerank: apiNode.pagerank,
              betweenness: apiNode.betweenness,
              communityId: apiNode.community_id,
              isTest: apiNode.is_test,
              isEntryPoint: apiNode.is_entry_point,
              hasDoc: apiNode.has_doc,
              // View-specific fields (dead code, hot files)
              ...("confidence_group" in apiNode && { confidenceGroup: (apiNode as Record<string, unknown>).confidence_group }),
              ...("commit_count" in apiNode && { commitCount: (apiNode as Record<string, unknown>).commit_count }),
            } satisfies FileNodeData,
          });
        }
      }
    }
  }

  extractNodes(layout);

  // Build React Flow edges
  for (const e of dedupedEdges) {
    rfEdges.push({
      id: `${e.source}→${e.target}`,
      source: e.source,
      target: e.target,
      type: "dependency",
      data: {
        importedNames: e.importedNames,
        edgeCount: e.edgeCount,
      } satisfies DependencyEdgeData,
    });
  }

  return { nodes: rfNodes, edges: rfEdges };
}

// ---- Layout for module-level graph ----

export async function layoutModuleGraph(
  moduleNodes: ModuleNodeResponse[],
  moduleEdges: ModuleEdgeResponse[],
  fileEntries?: Map<string, GraphNodeResponse>,
): Promise<{ nodes: Node[]; edges: Edge[] }> {
  if (moduleNodes.length === 0) return { nodes: [], edges: [] };

  const elkGraph: ElkNode = {
    id: "root",
    layoutOptions: {
      "elk.algorithm": "layered",
      "elk.direction": "DOWN",
      "elk.layered.spacing.nodeNodeBetweenLayers": "80",
      "elk.layered.spacing.edgeNodeBetweenLayers": "40",
      "elk.spacing.nodeNode": "40",
      "elk.spacing.componentComponent": "60",
      "elk.layered.crossingMinimization.strategy": "LAYER_SWEEP",
    },
    children: moduleNodes.map((m) => {
      const isFile = fileEntries?.has(m.module_id) ?? false;
      return {
        id: m.module_id,
        width: isFile ? FILE_NODE_WIDTH : MODULE_NODE_WIDTH,
        height: isFile ? FILE_NODE_HEIGHT : MODULE_NODE_HEIGHT,
        labels: [{ text: m.module_id }],
      };
    }),
    edges: moduleEdges.map((e, i) => ({
      id: `me${i}`,
      sources: [e.source],
      targets: [e.target],
    })),
  };

  const layout = await elk.layout(elkGraph);

  const rfNodes: Node[] = (layout.children ?? []).map((child) => {
    const apiNode = moduleNodes.find((m) => m.module_id === child.id)!;
    const fileEntry = fileEntries?.get(child.id);
    const isFile = !!fileEntry;
    const label = child.id.split("/").pop() ?? child.id;

    if (isFile) {
      // Render as a FileNode
      return {
        id: child.id,
        type: "fileNode",
        position: { x: child.x ?? 0, y: child.y ?? 0 },
        data: {
          label,
          fullPath: child.id,
          language: fileEntry.language,
          symbolCount: fileEntry.symbol_count,
          pagerank: fileEntry.pagerank,
          betweenness: fileEntry.betweenness,
          communityId: fileEntry.community_id,
          isTest: fileEntry.is_test,
          isEntryPoint: fileEntry.is_entry_point,
          hasDoc: fileEntry.has_doc,
          ...("confidence_group" in fileEntry && { confidenceGroup: (fileEntry as Record<string, unknown>).confidence_group }),
          ...("commit_count" in fileEntry && { commitCount: (fileEntry as Record<string, unknown>).commit_count }),
        } satisfies FileNodeData,
      };
    }

    return {
      id: child.id,
      type: "moduleGroup",
      position: { x: child.x ?? 0, y: child.y ?? 0 },
      data: {
        label,
        fullPath: child.id,
        fileCount: apiNode.file_count,
        symbolCount: apiNode.symbol_count,
        avgPagerank: apiNode.avg_pagerank,
        docCoveragePct: apiNode.doc_coverage_pct,
      } satisfies ModuleNodeData,
    };
  });

  // Deduplicate module edges
  const edgeMap = new Map<string, number>();
  for (const e of moduleEdges) {
    const key = `${e.source}→${e.target}`;
    edgeMap.set(key, (edgeMap.get(key) ?? 0) + e.edge_count);
  }

  const rfEdges: Edge[] = Array.from(edgeMap.entries()).map(([key, count]) => {
    const [source, target] = key.split("→");
    return {
      id: key,
      source,
      target,
      type: "dependency",
      data: {
        importedNames: [],
        edgeCount: count,
      } satisfies DependencyEdgeData,
    };
  });

  return { nodes: rfNodes, edges: rfEdges };
}
