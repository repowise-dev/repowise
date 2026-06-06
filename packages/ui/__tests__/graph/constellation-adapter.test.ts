import { describe, it, expect } from "vitest";
import {
  architectureToGraphology,
  hubNodeId,
  CORE_NODE_ID,
  mergeCommunitySlice,
  satelliteSizeFromPagerank,
} from "../../src/graph/sigma/constellation-adapter";
import type {
  ArchitectureGraph,
  ArchitectureNode,
  CommunitySlice,
  CommunitySliceNode,
} from "@repowise-dev/types/graph";

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

// ---------------------------------------------------------------------------
// G4: slice → satellite merge / collapse
// ---------------------------------------------------------------------------

function makeMember(
  node_id: string,
  overrides: Partial<CommunitySliceNode> = {},
): CommunitySliceNode {
  return {
    node_id,
    node_type: "file",
    language: "typescript",
    symbol_count: 2,
    pagerank: 0.1,
    betweenness: 0,
    community_id: 0,
    is_test: false,
    is_entry_point: false,
    has_doc: false,
    ...overrides,
  };
}

function makeSlice(overrides: Partial<CommunitySlice> = {}): CommunitySlice {
  return {
    community_id: 0,
    member_count: 2,
    nodes: [makeMember("src/mod0/a.ts"), makeMember("src/mod0/b.ts")],
    links: [],
    ...overrides,
  };
}

describe("satelliteSizeFromPagerank", () => {
  it("clamps to the 6–14 file-node band", () => {
    expect(satelliteSizeFromPagerank(0)).toBe(6);
    expect(satelliteSizeFromPagerank(10)).toBe(14);
    expect(satelliteSizeFromPagerank(0.25)).toBeCloseTo(10);
  });
});

describe("mergeCommunitySlice", () => {
  function baseGraph() {
    return architectureToGraphology(
      makeArch([
        makeArchNode({ community_id: 0, member_count: 2 }),
        makeArchNode({ community_id: 1, member_count: 2 }),
      ]),
    );
  }

  it("adds satellite file nodes around the hub", () => {
    const g = baseGraph();
    const before = g.order;
    const { satelliteIds } = mergeCommunitySlice(g, 0, makeSlice());
    expect(satelliteIds).toEqual(["src/mod0/a.ts", "src/mod0/b.ts"]);
    expect(g.order).toBe(before + 2);
    const sat = g.getNodeAttributes("src/mod0/a.ts");
    expect(sat.nodeType).toBe("file");
    expect(sat.communityId).toBe(0);
    expect(sat.fullPath).toBe("src/mod0/a.ts");
    expect(sat.size).toBeGreaterThanOrEqual(6);
    expect(sat.size).toBeLessThanOrEqual(14);
  });

  it("places satellites near their hub (deterministic)", () => {
    const g1 = baseGraph();
    const g2 = baseGraph();
    mergeCommunitySlice(g1, 0, makeSlice());
    mergeCommunitySlice(g2, 0, makeSlice());
    const a1 = g1.getNodeAttributes("src/mod0/a.ts");
    const a2 = g2.getNodeAttributes("src/mod0/a.ts");
    expect(a1.x).toBe(a2.x);
    expect(a1.y).toBe(a2.y);
    // Satellites cluster around the hub, not at the origin.
    const hub = g1.getNodeAttributes(hubNodeId(0));
    expect(Math.hypot(a1.x - hub.x, a1.y - hub.y)).toBeLessThan(200);
  });

  it("adds thin internal edges for intra-community links", () => {
    const g = baseGraph();
    mergeCommunitySlice(
      g,
      0,
      makeSlice({
        links: [
          { source: "src/mod0/a.ts", target: "src/mod0/b.ts", imported_names: [] },
        ],
      }),
    );
    expect(g.hasEdge("slice:src/mod0/a.ts→src/mod0/b.ts")).toBe(true);
    const e = g.getEdgeAttributes("slice:src/mod0/a.ts→src/mod0/b.ts");
    expect(e.edgeKind).toBe("internal");
  });

  it("adds a faint membership spoke for members with no intra edge", () => {
    const g = baseGraph();
    mergeCommunitySlice(g, 0, makeSlice()); // no links
    // Both members get a spoke to the hub.
    expect(g.hasEdge(`spoke:${hubNodeId(0)}→src/mod0/a.ts`)).toBe(true);
    expect(g.hasEdge(`spoke:${hubNodeId(0)}→src/mod0/b.ts`)).toBe(true);
    expect(
      g.getEdgeAttributes(`spoke:${hubNodeId(0)}→src/mod0/a.ts`).edgeKind,
    ).toBe("lowConfidence");
  });

  it("does not spoke members that already have an intra edge", () => {
    const g = baseGraph();
    mergeCommunitySlice(
      g,
      0,
      makeSlice({
        links: [
          { source: "src/mod0/a.ts", target: "src/mod0/b.ts", imported_names: [] },
        ],
      }),
    );
    expect(g.hasEdge(`spoke:${hubNodeId(0)}→src/mod0/a.ts`)).toBe(false);
    expect(g.hasEdge(`spoke:${hubNodeId(0)}→src/mod0/b.ts`)).toBe(false);
  });

  it("pulls in boundary stubs and cross-cluster edges", () => {
    const g = baseGraph();
    mergeCommunitySlice(
      g,
      0,
      makeSlice({
        nodes: [
          makeMember("src/mod0/a.ts"),
          makeMember("src/mod1/x.ts", { is_boundary: true, community_id: 1 }),
        ],
        member_count: 1,
        links: [
          { source: "src/mod0/a.ts", target: "src/mod1/x.ts", imported_names: [] },
        ],
      }),
    );
    expect(g.hasNode("src/mod1/x.ts")).toBe(true);
    expect(g.getNodeAttributes("src/mod1/x.ts").size).toBe(3); // tiny stub
    const e = g.getEdgeAttributes("slice:src/mod0/a.ts→src/mod1/x.ts");
    expect(e.edgeKind).toBe("crossCommunity");
  });

  it("is idempotent: re-merging the same slice adds nothing new", () => {
    const g = baseGraph();
    mergeCommunitySlice(g, 0, makeSlice());
    const order = g.order;
    const size = g.size;
    mergeCommunitySlice(g, 0, makeSlice());
    expect(g.order).toBe(order);
    expect(g.size).toBe(size);
  });

  it("no-ops when the hub is absent", () => {
    const g = baseGraph();
    const { satelliteIds } = mergeCommunitySlice(g, 42, makeSlice({ community_id: 42 }));
    expect(satelliteIds).toEqual([]);
  });
});
