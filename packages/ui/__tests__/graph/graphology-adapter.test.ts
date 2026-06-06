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
