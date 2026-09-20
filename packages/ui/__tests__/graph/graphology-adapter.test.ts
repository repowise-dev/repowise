import { describe, it, expect } from "vitest";
import { fileGraphToGraphology, fileGraphToGraphologyAsync, moduleGraphToGraphology } from "../../src/graph/sigma/graphology-adapter";
import type { GraphExport, GraphNode, GraphLink, ModuleGraph, ModuleNode, ModuleEdge, CommunitySummaryItem } from "@repowise-dev/types/graph";

function makeNode(overrides: Partial<GraphNode> & { node_id: string }): GraphNode {
  return {
    node_type: "file",
    language: "typescript",
    symbol_count: 0,
    pagerank: 0,
    betweenness: 0,
    community_id: 0,
    is_test: false,
    is_entry_point: false,
    has_doc: false,
    ...overrides,
  };
}

function makeFileGraph(
  nodes: GraphNode[] = [],
  links: GraphLink[] = [],
): GraphExport {
  return { nodes, links };
}

function makeModuleGraph(
  nodes: ModuleNode[] = [],
  edges: ModuleEdge[] = [],
): ModuleGraph {
  return { nodes, edges };
}

describe("fileGraphToGraphology", () => {
  it("handles an empty graph", () => {
    const g = fileGraphToGraphology(makeFileGraph());
    expect(g.order).toBe(0);
    expect(g.size).toBe(0);
  });

  it("adds nodes with no edges", () => {
    const g = fileGraphToGraphology(
      makeFileGraph([
        makeNode({
          node_id: "src/app.ts",
          symbol_count: 5,
          pagerank: 0.1,
          betweenness: 0.05,
          is_entry_point: true,
        }),
      ]),
    );
    expect(g.order).toBe(1);
    expect(g.size).toBe(0);
    expect(g.hasNode("src/app.ts")).toBe(true);
  });

  it("adds nodes with community data", () => {
    const g = fileGraphToGraphology(
      makeFileGraph([
        makeNode({
          node_id: "src/a.ts",
          symbol_count: 3,
          pagerank: 0.2,
          betweenness: 0.1,
          community_id: 2,
          has_doc: true,
        }),
      ]),
    );
    const attrs = g.getNodeAttributes("src/a.ts");
    expect(attrs.communityId).toBe(2);
    expect(attrs.hasDoc).toBe(true);
  });

  it("handles duplicate edges gracefully", () => {
    const nodes = [
      makeNode({ node_id: "a" }),
      makeNode({ node_id: "b" }),
    ];
    const links: GraphLink[] = [
      { source: "a", target: "b", imported_names: ["foo"] },
      { source: "a", target: "b", imported_names: ["bar"] },
    ];
    const g = fileGraphToGraphology(makeFileGraph(nodes, links));
    expect(g.order).toBe(2);
    expect(g.size).toBe(1);
  });

  it("handles nodes with missing optional fields", () => {
    const g = fileGraphToGraphology(
      makeFileGraph([makeNode({ node_id: "src/minimal.ts" })]),
    );
    expect(g.order).toBe(1);
    const attrs = g.getNodeAttributes("src/minimal.ts");
    expect(attrs.nodeType).toBe("file");
  });

  it("async variant produces a graph matching the sync builder across chunks", async () => {
    // Span multiple chunk boundaries (CHUNK_SIZE === 500).
    const nodes = Array.from({ length: 1200 }, (_, i) =>
      makeNode({ node_id: `src/f${i}.ts`, community_id: i % 7 }),
    );
    const links: GraphLink[] = Array.from({ length: 1100 }, (_, i) => ({
      source: `src/f${i}.ts`,
      target: `src/f${i + 1}.ts`,
      imported_names: ["x"],
    }));
    const graph = makeFileGraph(nodes, links);
    const sync = fileGraphToGraphology(graph);
    const async = await fileGraphToGraphologyAsync(graph);
    expect(async.order).toBe(sync.order);
    expect(async.size).toBe(sync.size);
  });
});

describe("moduleGraphToGraphology", () => {
  it("handles an empty graph", () => {
    const g = moduleGraphToGraphology(makeModuleGraph());
    expect(g.order).toBe(0);
    expect(g.size).toBe(0);
  });

  it("adds module nodes with no edges", () => {
    const g = moduleGraphToGraphology(
      makeModuleGraph([
        { module_id: "src", file_count: 10, symbol_count: 50, avg_pagerank: 0.5, doc_coverage_pct: 0.8 },
      ]),
    );
    expect(g.order).toBe(1);
    expect(g.hasNode("src")).toBe(true);
    expect(g.getNodeAttributes("src").nodeType).toBe("module");
  });

  it("maps community colors from community summaries", () => {
    const g = moduleGraphToGraphology(
      makeModuleGraph([
        { module_id: "src/auth", file_count: 5, symbol_count: 20, avg_pagerank: 0.3, doc_coverage_pct: 0.5 },
      ]),
      {
        communities: [
          { community_id: 3, top_file: "src/auth/login.ts", label: "auth", cohesion: 0.8, member_count: 5 },
        ],
      },
    );
    expect(g.order).toBe(1);
    const attrs = g.getNodeAttributes("src/auth");
    expect(attrs.communityId).toBe(3);
  });
});

describe("fileGraphToGraphology on a community slice", () => {
  // A slice is one community by definition, so the whole-repo seed — which
  // partitions by `community_id` and rings the centroids — degenerates: the
  // members all share one id and land in a single small disc while the
  // boundary stubs, which carry their own real ids, are strewn across the rest
  // of the ring. On the 94-node slice this was reported from, the members
  // occupied 3.4% of the drawn area.
  const members = Array.from({ length: 12 }, (_, i) =>
    makeNode({ node_id: `src/auth/f${i}.ts`, community_id: 4 }),
  );
  const stubs = [
    makeNode({ node_id: "src/db/pool.ts", community_id: 9 }),
    makeNode({ node_id: "src/http/router.ts", community_id: 17 }),
  ];
  const boundaryNodeIds = new Set(stubs.map((n) => n.node_id));
  const links: GraphLink[] = [
    { source: "src/auth/f0.ts", target: "src/db/pool.ts", imported_names: ["pool"] },
    { source: "src/auth/f1.ts", target: "src/http/router.ts", imported_names: ["router"] },
  ];

  const radius = (g: ReturnType<typeof fileGraphToGraphology>, id: string) => {
    const a = g.getNodeAttributes(id);
    return Math.hypot(a.x, a.y);
  };

  it("gives the members most of the field instead of a dot in the corner", () => {
    const g = fileGraphToGraphology(makeFileGraph([...members, ...stubs], links), {
      boundaryNodeIds,
    });
    const memberRadii = members.map((n) => radius(g, n.node_id));
    const stubRadii = stubs.map((n) => radius(g, n.node_id));
    const widest = Math.max(...memberRadii, ...stubRadii);

    // Members reach out to roughly 60% of the drawn radius, so their own
    // structure is the subject rather than a speck at the centre.
    expect(Math.max(...memberRadii) / widest).toBeGreaterThan(0.5);
    // Every stub sits outside every member, so the ring reads as the edge of
    // the world rather than as more of the same graph.
    expect(Math.min(...stubRadii)).toBeGreaterThan(Math.max(...memberRadii));
  });

  it("puts a stub on the bearing of the member it attaches to", () => {
    const g = fileGraphToGraphology(makeFileGraph([...members, ...stubs], links), {
      boundaryNodeIds,
    });
    const bearing = (id: string) => {
      const a = g.getNodeAttributes(id);
      return Math.atan2(a.y, a.x);
    };
    const delta = (a: number, b: number) =>
      Math.abs(Math.atan2(Math.sin(a - b), Math.cos(a - b)));
    // Within the ring's own jitter band of the member that pulls it, so an
    // edge leaving the group is a short spoke rather than a chord across it.
    expect(delta(bearing("src/db/pool.ts"), bearing("src/auth/f0.ts"))).toBeLessThan(0.2);
    expect(delta(bearing("src/http/router.ts"), bearing("src/auth/f1.ts"))).toBeLessThan(0.2);
  });

  it("leaves the whole-repo seed alone when no boundary set is given", () => {
    const withSlice = fileGraphToGraphology(makeFileGraph([...members, ...stubs], links), {
      boundaryNodeIds,
    });
    const withoutSlice = fileGraphToGraphology(makeFileGraph([...members, ...stubs], links));
    // The community partition still runs for every other caller — the slice
    // branch is gated on `boundaryNodeIds` and nothing else.
    expect(withoutSlice.getNodeAttributes("src/auth/f0.ts").x).not.toBe(
      withSlice.getNodeAttributes("src/auth/f0.ts").x,
    );
  });

  it("classifies no edge as the unreachable import kind", () => {
    const g = fileGraphToGraphology(makeFileGraph([...members, ...stubs], links), {
      boundaryNodeIds,
    });
    const kinds = new Set<string>();
    g.forEachEdge((_, attrs) => kinds.add(attrs.edgeKind));
    expect(kinds.has("import" as never)).toBe(false);
    // Member-to-stub edges cross communities; that is what the ring encodes.
    expect(kinds.has("crossCommunity")).toBe(true);
  });
});

describe("fileGraphToGraphology slice seed degenerate cases", () => {
  const boundary = (ids: string[]) => new Set(ids);
  const at = (g: ReturnType<typeof fileGraphToGraphology>, id: string) => {
    const a = g.getNodeAttributes(id);
    return { r: Math.hypot(a.x, a.y), t: Math.atan2(a.y, a.x) };
  };

  it("puts a lone member at the centre, not two thirds of the way to the rim", () => {
    // `sqrt((i + 1) / n)` gave member 0 the full radius, so a one-file
    // community drew its one file out on the 3 o'clock ray with every stub
    // stacked on that same bearing and the rest of the field empty.
    const nodes = [
      makeNode({ node_id: "src/only.ts", community_id: 4 }),
      makeNode({ node_id: "vendor/a.ts", community_id: 9 }),
      makeNode({ node_id: "vendor/b.ts", community_id: 9 }),
    ];
    const links: GraphLink[] = [
      { source: "src/only.ts", target: "vendor/a.ts", imported_names: ["a"] },
      { source: "src/only.ts", target: "vendor/b.ts", imported_names: ["b"] },
    ];
    const g = fileGraphToGraphology(makeFileGraph(nodes, links), {
      boundaryNodeIds: boundary(["vendor/a.ts", "vendor/b.ts"]),
    });
    const member = at(g, "src/only.ts");
    const rim = Math.max(at(g, "vendor/a.ts").r, at(g, "vendor/b.ts").r);
    expect(member.r / rim).toBeLessThan(0.05);
  });

  it("fans stubs that all hang off one member instead of stacking them", () => {
    // The seed IS the layout — FA2 and noverlap are both off for a file graph
    // — so nothing separates two stubs that resolve to the same bearing later.
    const members = Array.from({ length: 6 }, (_, i) =>
      makeNode({ node_id: `src/m${i}.ts`, community_id: 4 }),
    );
    const stubs = Array.from({ length: 20 }, (_, i) =>
      makeNode({ node_id: `vendor/s${i}.ts`, community_id: 9 }),
    );
    // Every stub attaches to the same high-degree member.
    const links: GraphLink[] = stubs.map((s) => ({
      source: "src/m0.ts",
      target: s.node_id,
      imported_names: ["x"],
    }));
    const g = fileGraphToGraphology(makeFileGraph([...members, ...stubs], links), {
      boundaryNodeIds: boundary(stubs.map((s) => s.node_id)),
    });
    const angles = stubs.map((s) => at(g, s.node_id).t).sort((a, b) => a - b);
    const spans = angles.at(-1)! - angles[0]!;
    // A band wide enough to seat twenty, not the old fixed ±0.1 rad box.
    expect(spans).toBeGreaterThan(1);
    // And no two of them land on the same bearing.
    expect(new Set(angles.map((a) => a.toFixed(4))).size).toBe(stubs.length);
  });

  it("never emits a non-finite coordinate", () => {
    const nodes = [
      makeNode({ node_id: "src/a.ts", community_id: 4 }),
      makeNode({ node_id: "src/b.ts", community_id: 4 }),
      makeNode({ node_id: "vendor/x.ts", community_id: 9 }),
      makeNode({ node_id: "vendor/y.ts", community_id: 9 }),
    ];
    const links: GraphLink[] = [
      // A stub pulled in exactly opposite directions, which sums to a float
      // residue rather than to zero.
      { source: "src/a.ts", target: "vendor/x.ts", imported_names: ["x"] },
      { source: "src/b.ts", target: "vendor/x.ts", imported_names: ["x"] },
      // A self-link, and a link naming a node the graph does not carry.
      { source: "vendor/y.ts", target: "vendor/y.ts", imported_names: ["y"] },
      { source: "src/a.ts", target: "src/gone.ts", imported_names: ["g"] },
    ];
    const g = fileGraphToGraphology(makeFileGraph(nodes, links), {
      boundaryNodeIds: boundary(["vendor/x.ts", "vendor/y.ts"]),
    });
    g.forEachNode((_, a) => {
      expect(Number.isFinite(a.x)).toBe(true);
      expect(Number.isFinite(a.y)).toBe(true);
    });
  });

  it("does not seed a slice when the node count makes the field zero-sized", () => {
    // `nodeCount` is a public option. At 0 the spread is 0 and every seeded
    // coordinate would collapse onto the origin, with no FA2 to rescue it.
    const nodes = [
      makeNode({ node_id: "src/a.ts", community_id: 4 }),
      makeNode({ node_id: "vendor/x.ts", community_id: 9 }),
    ];
    const g = fileGraphToGraphology(makeFileGraph(nodes, []), {
      boundaryNodeIds: boundary(["vendor/x.ts"]),
      nodeCount: 0,
    });
    g.forEachNode((_, a) => {
      expect(Number.isFinite(a.x)).toBe(true);
      expect(Number.isFinite(a.y)).toBe(true);
    });
  });
});
