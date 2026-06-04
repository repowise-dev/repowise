import { describe, it, expect } from "vitest";
import {
  architectureToGraphology,
  hubNodeId,
  CORE_NODE_ID,
} from "../../src/graph/sigma/constellation-adapter";
import type { ArchitectureGraph, ArchitectureNode } from "@repowise-dev/types/graph";

function makeArchNode(
  overrides: Partial<ArchitectureNode> & { community_id: number },
): ArchitectureNode {
  return {
    label: `community-${overrides.community_id}`,
    cohesion: 0.5,
    member_count: 10,
    top_file: `src/mod${overrides.community_id}/index.ts`,
    avg_pagerank: 0.1,
    hotspot_count: 0,
    dead_count: 0,
    has_decision: false,
    doc_coverage_pct: 0.5,
    languages: ["typescript"],
    ...overrides,
  };
}

function makeArch(
  nodes: ArchitectureNode[] = [],
  edges: ArchitectureGraph["edges"] = [],
): ArchitectureGraph {
  return { nodes, edges };
}

describe("architectureToGraphology", () => {
  it("handles an empty architecture graph (just the core node)", () => {
    const g = architectureToGraphology(makeArch());
    expect(g.order).toBe(1); // repo-core only
    expect(g.hasNode(CORE_NODE_ID)).toBe(true);
    expect(g.size).toBe(0);
  });

  it("adds one hub node per community plus the repo-core", () => {
    const g = architectureToGraphology(
      makeArch([
        makeArchNode({ community_id: 0 }),
        makeArchNode({ community_id: 1 }),
      ]),
    );
    expect(g.order).toBe(3); // 2 hubs + core
    expect(g.hasNode(hubNodeId(0))).toBe(true);
    expect(g.hasNode(hubNodeId(1))).toBe(true);
    expect(g.hasNode(CORE_NODE_ID)).toBe(true);
  });

  it("marks hubs with nodeType=hub, forceLabel, and an uppercase label", () => {
    const g = architectureToGraphology(
      makeArch([makeArchNode({ community_id: 0, label: "auth core" })]),
    );
    const attrs = g.getNodeAttributes(hubNodeId(0));
    expect(attrs.nodeType).toBe("hub");
    expect(attrs.forceLabel).toBe(true);
    expect(attrs.label).toBe("AUTH CORE");
    expect(attrs.memberCount).toBe(10);
  });

  it("falls back to dirname then Community N when label is blank", () => {
    const fromFile = architectureToGraphology(
      makeArch([
        makeArchNode({ community_id: 0, label: "", top_file: "src/payments/api.ts" }),
      ]),
    );
    expect(fromFile.getNodeAttributes(hubNodeId(0)).label).toBe("PAYMENTS");

    const fromId = architectureToGraphology(
      makeArch([makeArchNode({ community_id: 7, label: "", top_file: "" })]),
    );
    expect(fromId.getNodeAttributes(hubNodeId(7)).label).toBe("COMMUNITY 7");
  });

  it("sizes hubs within the 14–32 sigma-unit range", () => {
    const g = architectureToGraphology(
      makeArch([
        makeArchNode({ community_id: 0, member_count: 1 }),
        makeArchNode({ community_id: 1, member_count: 5000 }),
      ]),
    );
    const small = g.getNodeAttributes(hubNodeId(0)).size;
    const big = g.getNodeAttributes(hubNodeId(1)).size;
    expect(small).toBeGreaterThanOrEqual(14);
    expect(big).toBeLessThanOrEqual(32);
    expect(big).toBeGreaterThan(small);
  });

  it("builds cross-community edges with crossCommunity kind and clamped size", () => {
    const g = architectureToGraphology(
      makeArch(
        [
          makeArchNode({ community_id: 0 }),
          makeArchNode({ community_id: 1 }),
        ],
        [{ source: 0, target: 1, edge_count: 7 }],
      ),
    );
    expect(g.size).toBe(1);
    const edgeKey = hubNodeId(0) + "→" + hubNodeId(1);
    const attrs = g.getEdgeAttributes(edgeKey);
    expect(attrs.edgeKind).toBe("crossCommunity");
    expect(attrs.size).toBeGreaterThanOrEqual(1.5);
    expect(attrs.size).toBeLessThanOrEqual(2.5);
    expect(attrs.edgeCount).toBe(7);
  });

  it("skips edges referencing dropped communities", () => {
    const g = architectureToGraphology(
      makeArch(
        [makeArchNode({ community_id: 0 })],
        [{ source: 0, target: 99, edge_count: 3 }],
      ),
    );
    expect(g.size).toBe(0);
  });

  it("places the core at the origin and labels it with the repo name", () => {
    const g = architectureToGraphology(makeArch([makeArchNode({ community_id: 0 })]), {
      repoName: "repowise",
    });
    const core = g.getNodeAttributes(CORE_NODE_ID);
    expect(core.nodeType).toBe("core");
    expect(core.x).toBe(0);
    expect(core.y).toBe(0);
    expect(core.label).toBe("REPOWISE");
  });

  it("adds hub→core spokes only when requested", () => {
    const base = architectureToGraphology(
      makeArch([makeArchNode({ community_id: 0 })]),
    );
    expect(base.size).toBe(0);
    const withSpokes = architectureToGraphology(
      makeArch([makeArchNode({ community_id: 0 })]),
      { includeCoreSpokes: true },
    );
    expect(withSpokes.size).toBe(1);
    expect(withSpokes.hasEdge(hubNodeId(0) + "→" + CORE_NODE_ID)).toBe(true);
  });

  it("is deterministic across builds", () => {
    const arch = makeArch(
      [makeArchNode({ community_id: 0 }), makeArchNode({ community_id: 1 })],
      [{ source: 0, target: 1, edge_count: 2 }],
    );
    const a = architectureToGraphology(arch);
    const b = architectureToGraphology(arch);
    a.forEachNode((node, attrs) => {
      expect(b.getNodeAttributes(node).x).toBe(attrs.x);
      expect(b.getNodeAttributes(node).y).toBe(attrs.y);
    });
  });
});
