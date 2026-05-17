"use client";

/**
 * Compute React Flow nodes + edges for a given C4 view.
 *
 * Pure transform from backend payload → React Flow inputs, threaded through
 * the async ELK layout call. Memoized so a level switch or selection change
 * doesn't re-layout.
 */

import { useEffect, useState } from "react";
import type { Edge, Node } from "@xyflow/react";
import {
  C4_NODE_SIZES,
  computeC4Layout,
  type C4LayoutNode,
} from "../layout/elk-c4-layout";
import type {
  C4EdgeData,
  C4L1,
  C4L2,
  C4L3,
  C4Level,
  C4NodeData,
  C4Relation,
} from "../types";

interface BuiltInputs {
  nodes: { id: string; type: keyof typeof C4_NODE_SIZES; data: C4NodeData }[];
  edges: { id: string; source: string; target: string; relation: C4Relation }[];
}

function buildL1(view: C4L1): BuiltInputs {
  return {
    nodes: [
      ...view.people.map((p) => ({
        id: p.id,
        type: "person" as const,
        data: { kind: "person" as const, person: p },
      })),
      {
        id: view.system.id,
        type: "system" as const,
        data: { kind: "system" as const, system: view.system },
      },
      ...view.external_systems.map((e) => ({
        id: e.id,
        type: "external" as const,
        data: { kind: "external" as const, external: e },
      })),
    ],
    edges: view.relations.map((r, i) => ({
      id: `e${i}:${r.source_id}->${r.target_id}`,
      source: r.source_id,
      target: r.target_id,
      relation: r,
    })),
  };
}

function buildL2(view: C4L2): BuiltInputs {
  return {
    nodes: [
      ...view.containers.map((c) => ({
        id: c.id,
        type: "container" as const,
        data: { kind: "container" as const, container: c },
      })),
      ...view.external_systems.map((e) => ({
        id: e.id,
        type: "external" as const,
        data: { kind: "external" as const, external: e },
      })),
    ],
    edges: view.relations.map((r, i) => ({
      id: `e${i}:${r.source_id}->${r.target_id}`,
      source: r.source_id,
      target: r.target_id,
      relation: r,
    })),
  };
}

function buildL3(view: C4L3): BuiltInputs {
  return {
    nodes: [
      ...view.components.map((c) => ({
        id: c.id,
        type: "component" as const,
        data: { kind: "component" as const, component: c },
      })),
      ...view.external_systems.map((e) => ({
        id: e.id,
        type: "external" as const,
        data: { kind: "external" as const, external: e },
      })),
    ],
    edges: view.relations.map((r, i) => ({
      id: `e${i}:${r.source_id}->${r.target_id}`,
      source: r.source_id,
      target: r.target_id,
      relation: r,
    })),
  };
}

export interface C4LayoutResult {
  nodes: Node[];
  edges: Edge[];
  loading: boolean;
}

export function useC4Layout(
  level: C4Level,
  view: C4L1 | C4L2 | C4L3 | null,
): C4LayoutResult {
  const [result, setResult] = useState<C4LayoutResult>({ nodes: [], edges: [], loading: false });

  useEffect(() => {
    if (!view) {
      setResult({ nodes: [], edges: [], loading: false });
      return;
    }
    let cancelled = false;
    setResult((r) => ({ ...r, loading: true }));

    const built =
      level === 1
        ? buildL1(view as C4L1)
        : level === 2
        ? buildL2(view as C4L2)
        : buildL3(view as C4L3);

    const layoutNodes: C4LayoutNode[] = built.nodes.map((n) => ({
      id: n.id,
      width: C4_NODE_SIZES[n.type].width,
      height: C4_NODE_SIZES[n.type].height,
    }));
    const layoutEdges = built.edges.map((e) => ({ id: e.id, source: e.source, target: e.target }));

    void computeC4Layout(layoutNodes, layoutEdges).then((positions) => {
      if (cancelled) return;
      const nodes: Node[] = built.nodes.map((n) => {
        const pos = positions.get(n.id) ?? { x: 0, y: 0, width: 0, height: 0 };
        return {
          id: n.id,
          type: n.type,
          position: { x: pos.x, y: pos.y },
          data: n.data as unknown as Record<string, unknown>,
          width: C4_NODE_SIZES[n.type].width,
          height: C4_NODE_SIZES[n.type].height,
        };
      });
      const edges: Edge[] = built.edges.map((e) => ({
        id: e.id,
        source: e.source,
        target: e.target,
        type: "relation",
        data: { relation: e.relation } as unknown as C4EdgeData & Record<string, unknown>,
      }));
      setResult({ nodes, edges, loading: false });
    });

    return () => {
      cancelled = true;
    };
  }, [level, view]);

  return result;
}
