"use client";

import { useState, useEffect, useRef } from "react";
import type { Node, Edge } from "@xyflow/react";
import type {
  GraphNode as GraphNodeResponse,
  GraphLink as GraphEdgeResponse,
  ModuleNode as ModuleNodeResponse,
  ModuleEdge as ModuleEdgeResponse,
} from "@repowise-dev/types/graph";
import { layoutFileGraph, layoutModuleGraph } from "./elk-layout";

interface ElkLayoutResult {
  nodes: Node[];
  edges: Edge[];
  isLayouting: boolean;
}

/** Compute ELK layout for a file-level graph (ego, architecture, full, dead, hot). */
export function useFileElkLayout(
  graphNodes: GraphNodeResponse[] | undefined,
  graphEdges: GraphEdgeResponse[] | undefined,
): ElkLayoutResult {
  const [nodes, setNodes] = useState<Node[]>([]);
  const [edges, setEdges] = useState<Edge[]>([]);
  const [isLayouting, setIsLayouting] = useState(false);
  const cacheKey = useRef("");

  useEffect(() => {
    if (!graphNodes || !graphEdges || graphNodes.length === 0) {
      setNodes([]);
      setEdges([]);
      cacheKey.current = "";
      return;
    }

    const key = `${graphNodes.length}:${graphEdges.length}:${graphNodes[0]?.node_id}`;
    if (key === cacheKey.current) return;
    cacheKey.current = key;

    setIsLayouting(true);
    layoutFileGraph(graphNodes, graphEdges)
      .then(({ nodes: n, edges: e }) => {
        setNodes(n);
        setEdges(e);
      })
      .catch(() => {
        const cols = Math.ceil(Math.sqrt(graphNodes.length));
        setNodes(
          graphNodes.map((node, i) => ({
            id: node.node_id,
            type: "fileNode",
            position: { x: (i % cols) * 200, y: Math.floor(i / cols) * 80 },
            data: {
              label: node.node_id.split("/").pop() ?? node.node_id,
              fullPath: node.node_id,
              language: node.language,
              symbolCount: node.symbol_count,
              pagerank: node.pagerank,
              betweenness: node.betweenness,
              communityId: node.community_id,
              isTest: node.is_test,
              isEntryPoint: node.is_entry_point,
              hasDoc: node.has_doc,
              ...("confidence_group" in node && { confidenceGroup: (node as Record<string, unknown>).confidence_group }),
              ...("commit_count" in node && { commitCount: (node as Record<string, unknown>).commit_count }),
            },
          })),
        );
        setEdges([]);
      })
      .finally(() => setIsLayouting(false));
  }, [graphNodes, graphEdges]);

  return { nodes, edges, isLayouting };
}

/** Compute ELK layout for a module-level graph. Supports mixed modules + files via fileEntries. */
export function useModuleElkLayout(
  moduleNodes: ModuleNodeResponse[] | undefined,
  moduleEdges: ModuleEdgeResponse[] | undefined,
  fileEntries?: Map<string, GraphNodeResponse>,
): ElkLayoutResult {
  const [nodes, setNodes] = useState<Node[]>([]);
  const [edges, setEdges] = useState<Edge[]>([]);
  const [isLayouting, setIsLayouting] = useState(false);
  const cacheKey = useRef("");

  useEffect(() => {
    if (!moduleNodes || !moduleEdges || moduleNodes.length === 0) {
      setNodes([]);
      setEdges([]);
      cacheKey.current = "";
      return;
    }

    const key = `mod:${moduleNodes.length}:${moduleEdges.length}:${moduleNodes[0]?.module_id}`;
    if (key === cacheKey.current) return;
    cacheKey.current = key;

    setIsLayouting(true);
    layoutModuleGraph(moduleNodes, moduleEdges, fileEntries)
      .then(({ nodes: n, edges: e }) => {
        setNodes(n);
        setEdges(e);
      })
      .catch(() => {
        const cols = Math.ceil(Math.sqrt(moduleNodes.length));
        setNodes(
          moduleNodes.map((m, i) => ({
            id: m.module_id,
            type: "moduleGroup",
            position: { x: (i % cols) * 240, y: Math.floor(i / cols) * 100 },
            data: {
              label: m.module_id.split("/").pop() ?? m.module_id,
              fullPath: m.module_id,
              fileCount: m.file_count,
              symbolCount: m.symbol_count,
              avgPagerank: m.avg_pagerank,
              docCoveragePct: m.doc_coverage_pct,
            },
          })),
        );
        setEdges([]);
      })
      .finally(() => setIsLayouting(false));
  }, [moduleNodes, moduleEdges, fileEntries]);

  return { nodes, edges, isLayouting };
}
