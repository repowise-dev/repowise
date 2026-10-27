import { describe, expect, it } from "vitest";
import { buildAdjacency, servicesBetween, shortestPaths } from "../../src/workspace/system-map/between";
import type { EdgeFamily } from "../../src/workspace/system-map/edge-families";
import type { LevelsEdge, LevelsGraph } from "../../src/workspace/system-map/level-types";
import { ESHOP_GRAPH } from "./fixtures/eshop-system-graph";

function edge(source: string, target: string, kind: LevelsEdge["kind"] = "http", weight = 1): LevelsEdge {
  return { id: `${source}->${target}:${kind}`, source, target, kind, weight, strategy: null };
}

function graphOf(edges: LevelsEdge[]): LevelsGraph {
  const ids = new Set(edges.flatMap((e) => [e.source, e.target]));
  return { nodes: [...ids].map((id) => ({ id, name: id, repo: id })), edges };
}

describe("servicesBetween on eShop", () => {
  it("sums every direct link one way and reports none the other", () => {
    const r = servicesBetween(ESHOP_GRAPH, "webapp", "ordering.api")!;
    expect(r.aToB).toMatchObject({ links: 8, edges: 2, kinds: { event: 6, http: 2 } });
    expect(r.bToA).toBeNull();
    expect(r.pathsAToB).toHaveLength(1);
    expect(r.pathsAToB[0]!.nodes).toEqual(["webapp", "ordering.api"]);
    expect(r.pathsBToA).toEqual([]);
  });

  it("counts links a gateway route made", () => {
    const r = servicesBetween(ESHOP_GRAPH, "webapp", "catalog.api")!;
    expect(r.aToB).toMatchObject({ links: 2, gatewayLinks: 2 });
  });

  it("reports both directions of a cycle", () => {
    const r = servicesBetween(ESHOP_GRAPH, "catalog.api", "ordering.api")!;
    expect(r.aToB?.links).toBe(2);
    expect(r.bToA?.links).toBe(2);
  });

  it("finds the path through one service when there is no direct link", () => {
    const r = servicesBetween(ESHOP_GRAPH, "webhookclient", "catalog.api")!;
    expect(r.aToB).toBeNull();
    expect(r.pathsAToB).toHaveLength(1);
    const [path] = r.pathsAToB;
    expect(path!.nodes).toEqual(["webhookclient", "webhooks.api", "catalog.api"]);
    expect(path!.hops.map((h) => Object.keys(h.summary.kinds))).toEqual([["http"], ["event"]]);
  });

  it("walks the map's default families: a package edge is a route only when Build is on", () => {
    expect(servicesBetween(ESHOP_GRAPH, "identity.api", "eshop.servicedefaults")!.pathsAToB).toEqual([]);
    const build = servicesBetween(ESHOP_GRAPH, "identity.api", "eshop.servicedefaults", {
      families: new Set<EdgeFamily>(["build"]),
    })!;
    expect(build.aToB?.kinds).toEqual({ package: 1 });
  });

  it("returns null for an unknown service or the same service twice", () => {
    expect(servicesBetween(ESHOP_GRAPH, "webapp", "nope")).toBeNull();
    expect(servicesBetween(ESHOP_GRAPH, "webapp", "webapp")).toBeNull();
  });
});

describe("server-sent families", () => {
  it("follows an edge's family from the payload over the kind registry", () => {
    const g = graphOf([{ ...edge("a", "b", "http"), family: "build" }, { ...edge("b", "c", "package"), family: "runtime" }]);
    const r = servicesBetween(g, "a", "c")!;
    expect(r.pathsAToB).toEqual([]);
    expect(servicesBetween(g, "b", "c")!.aToB?.kinds).toEqual({ package: 1 });
  });

  it("falls back to the registry for a family it does not know", () => {
    const g = graphOf([{ ...edge("a", "b", "http"), family: "telepathy" as never }]);
    expect(servicesBetween(g, "a", "b")!.aToB?.links).toBe(1);
  });
});

describe("shortestPaths", () => {
  const families = new Set<EdgeFamily>(["runtime"]);

  it("ranks two-hop paths by their weakest hop, then by id, and caps them", () => {
    const g = graphOf([
      edge("a", "x", "http", 5),
      edge("x", "b", "http", 1),
      edge("a", "y", "http", 3),
      edge("y", "b", "event", 3),
      edge("a", "z", "http", 3),
      edge("z", "b", "http", 3),
      edge("a", "w", "http", 2),
      edge("w", "b", "http", 2),
    ]);
    const adj = buildAdjacency(g.edges, families);
    expect(shortestPaths(adj, "a", "b").map((p) => p.nodes[1])).toEqual(["y", "z", "w"]);
    expect(shortestPaths(adj, "a", "b", 2, 10).map((p) => p.nodes[1])).toEqual(["y", "z", "w", "x"]);
  });

  it("stops at one hop when asked, and goes no further than two", () => {
    const g = graphOf([edge("a", "x"), edge("x", "y"), edge("y", "b"), edge("x", "b")]);
    const adj = buildAdjacency(g.edges, families);
    expect(shortestPaths(adj, "a", "b", 1)).toEqual([]);
    expect(shortestPaths(adj, "a", "b").map((p) => p.nodes)).toEqual([["a", "x", "b"]]);
    expect(shortestPaths(adj, "a", "y")).toHaveLength(1);
    const far = graphOf([edge("a", "x"), edge("x", "y"), edge("y", "b")]);
    expect(shortestPaths(buildAdjacency(far.edges, families), "a", "b")).toEqual([]);
  });

  it("ignores self-edges and terminates on cycles", () => {
    const g = graphOf([edge("a", "a"), edge("a", "x"), edge("x", "a"), edge("x", "x"), edge("x", "b")]);
    const adj = buildAdjacency(g.edges, families);
    expect(adj.get("a")?.has("a")).toBe(false);
    expect(shortestPaths(adj, "a", "b").map((p) => p.nodes)).toEqual([["a", "x", "b"]]);
    expect(shortestPaths(adj, "b", "a")).toEqual([]);
  });
});
