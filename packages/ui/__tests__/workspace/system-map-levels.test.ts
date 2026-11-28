import { describe, expect, it } from "vitest";
import { buildLevelView, normalizeGraph } from "../../src/workspace/system-map/levels";
import {
  ESTATE_MORE_ID,
  TRAY_NODE_ID,
  activeGroups,
  groupNodeId,
} from "../../src/workspace/system-map/estate-view";
import { DEFAULT_EDGE_FAMILIES, type EdgeFamily } from "../../src/workspace/system-map/edge-families";
import type {
  DrawnBand,
  DrawnGroupNode,
  DrawnMoreNode,
  DrawnNode,
  DrawnTrayNode,
  LevelsEdge,
  LevelsGraph,
} from "../../src/workspace/system-map/level-types";
import { ESHOP_GRAPH } from "./fixtures/eshop-system-graph";

function edge(source: string, target: string, kind: LevelsEdge["kind"] = "http", weight = 1): LevelsEdge {
  return { id: `${source}->${target}:${kind}`, source, target, kind, weight, strategy: null };
}

/** `groups` groups of `size` services; each group is a chain, and group i calls group i+1. */
function groupedEstate(groups: number, size: number, extra: readonly LevelsEdge[] = []): LevelsGraph {
  const nodes = [];
  const edges: LevelsEdge[] = [];
  for (let g = 0; g < groups; g++) {
    for (let s = 0; s < size; s++) {
      nodes.push({ id: `g${g}.s${s}`, name: `g${g}.s${s}`, repo: `g${g}.s${s}`, group: `g${g}` });
      if (s > 0) edges.push(edge(`g${g}.s${s - 1}`, `g${g}.s${s}`));
    }
    if (g + 1 < groups) edges.push(edge(`g${g}.s0`, `g${g + 1}.s0`, "event", 3));
  }
  const meta = Array.from({ length: groups }, (_, g) => (g === 0 ? { id: "g0", label: "Storefront", source: "tag" } : { id: `g${g}` }));
  return { nodes, edges: [...edges, ...extra], groups: meta };
}

/** `n` ungrouped services in a ring of HTTP calls. */
function flatRing(n: number): LevelsGraph {
  const nodes = Array.from({ length: n }, (_, i) => ({ id: `s${String(i).padStart(4, "0")}`, name: `s${i}`, repo: `s${i}` }));
  const edges = nodes.map((node, i) => edge(node.id, nodes[(i + 1) % n]!.id, "http", 1 + (i % 7)));
  return { nodes, edges };
}

const ofType = <T extends DrawnNode["type"]>(nodes: DrawnNode[], type: T) =>
  nodes.filter((n): n is Extract<DrawnNode, { type: T }> => n.type === type);

describe("estate level on eShop (flat, under the grouping threshold)", () => {
  const view = buildLevelView(ESHOP_GRAPH);

  it("draws services directly with the unlinked ones in one tray", () => {
    expect(view.level).toBe("estate");
    expect(view.grouped).toBe(false);
    const tray = ofType(view.nodes, "tray")[0] as DrawnTrayNode;
    expect(tray.id).toBe(TRAY_NODE_ID);
    expect(tray.open).toBe(false);
    expect([...tray.serviceIds].sort()).toEqual([
      "eshop.servicedefaults",
      "eventbus",
      "eventbusrabbitmq",
      "identity.api",
      "integrationeventlogef",
      "ordering.domain",
      "ordering.infrastructure",
    ]);
    expect(ofType(view.nodes, "service")).toHaveLength(11);
    // Most-linked first: ordering.api carries 23 runtime links, catalog.api 22.
    expect(ofType(view.nodes, "service")[0]!.id).toBe("ordering.api");
    expect(view.truncation).toBeNull();
  });

  it("draws every runtime edge between two cards as itself and hides build by default", () => {
    expect(view.edges.every((e) => e.type === "edge")).toBe(true);
    expect(view.edges).toHaveLength(18);
    expect(view.familyCounts.build).toEqual({ edges: 26, links: 26 });
    expect(view.familyCounts.runtime?.edges).toBe(18);
    expect(view.familyCounts.data).toBeUndefined();
  });

  it("keeps both directions of a cycle", () => {
    const ids = view.edges.map((e) => e.id);
    expect(ids).toContain("catalog.api->ordering.api:event");
    expect(ids).toContain("ordering.api->catalog.api:event");
  });

  it("bands build links into the tray without moving any card", () => {
    const withBuild = buildLevelView(ESHOP_GRAPH, { families: new Set<EdgeFamily>(["runtime", "data", "build"]) });
    expect(withBuild.nodes).toEqual(view.nodes);
    const band = withBuild.edges.find((e) => e.id === `band:ordering.api>${TRAY_NODE_ID}`) as DrawnBand;
    expect(band.summary.links).toBe(5);
    expect(band.summary.kinds).toEqual({ package: 5 });
    // webapp -> webappcomponents joins two cards, so it stays a single edge.
    expect(withBuild.edges.find((e) => e.id === "webapp->webappcomponents:package")?.type).toBe("edge");
  });

  it("opens the tray into its own cards", () => {
    const open = buildLevelView(ESHOP_GRAPH, { openTray: true, families: new Set<EdgeFamily>(["runtime", "build"]) });
    expect(open.level).toBe("group");
    const tray = ofType(open.nodes, "tray")[0]!;
    expect(tray.open).toBe(true);
    const inside = ofType(open.nodes, "service").filter((n) => n.parentId === TRAY_NODE_ID);
    expect(inside).toHaveLength(7);
    expect(open.edges.every((e) => e.type === "edge")).toBe(true);
    // Container precedes its children, as React Flow requires.
    expect(open.nodes.indexOf(tray)).toBeLessThan(open.nodes.indexOf(inside[0]!));
  });
});

describe("grouping follows the payload", () => {
  it("stays flat when the payload carries no groups, whatever the nodes say", () => {
    const g = groupedEstate(4, 20);
    expect(activeGroups({ ...g, groups: undefined })).toBeNull();
    expect(activeGroups({ ...g, groups: [] })).toBeNull();
    expect(buildLevelView({ ...g, groups: null }).grouped).toBe(false);
  });

  it("trusts the server's groups on a small estate", () => {
    const g = groupedEstate(2, 3);
    expect(g.nodes).toHaveLength(6);
    const view = buildLevelView(g);
    expect(view.grouped).toBe(true);
    expect(view.nodes.map((n) => n.type)).toEqual(["group", "group"]);
  });

  it("draws a one-member group's service bare", () => {
    const g = groupedEstate(3, 20);
    const lone = { id: "lone", name: "lone", repo: "lone", group: "solo" };
    const view = buildLevelView({ ...g, nodes: [...g.nodes, lone], edges: [...g.edges, edge("lone", "g0.s0")] });
    expect(view.grouped).toBe(true);
    expect(view.nodes.find((n) => n.id === "lone")?.type).toBe("service");
    expect(view.nodes.some((n) => n.id === groupNodeId("solo"))).toBe(false);
  });
});

describe("estate level with groups", () => {
  const graph = groupedEstate(3, 20);

  it("draws one card per group and one band per ordered group pair", () => {
    const view = buildLevelView(graph);
    expect(view.grouped).toBe(true);
    expect(view.nodes.map((n) => n.type)).toEqual(["group", "group", "group"]);
    const g0 = view.nodes.find((n) => n.id === groupNodeId("g0")) as DrawnGroupNode;
    expect(g0.label).toBe("Storefront");
    expect(g0.source).toBe("tag");
    expect(g0.repoCount).toBe(20);
    expect(g0.linksOut).toBe(3);
    expect(g0.linksInside).toBe(19);
    expect(view.edges.map((e) => e.id)).toEqual(["band:group:g0>group:g1", "band:group:g1>group:g2"]);
  });

  it("keeps opposite bands apart when two groups call each other", () => {
    const view = buildLevelView(groupedEstate(3, 20, [edge("g1.s3", "g0.s4", "grpc", 2)]));
    const ids = view.edges.map((e) => e.id);
    expect(ids).toContain("band:group:g0>group:g1");
    expect(ids).toContain("band:group:g1>group:g0");
    const back = view.edges.find((e) => e.id === "band:group:g1>group:g0") as DrawnBand;
    expect(back.summary).toMatchObject({ links: 2, edges: 1, kinds: { grpc: 2 } });
  });

  it("opens a group into a container and bands its services to the closed groups", () => {
    const view = buildLevelView(graph, { open: ["g1", "nope"] });
    expect(view.level).toBe("group");
    const container = view.nodes.find((n) => n.id === groupNodeId("g1")) as DrawnGroupNode;
    expect(container.open).toBe(true);
    const children = ofType(view.nodes, "service").filter((n) => n.parentId === container.id);
    expect(children).toHaveLength(20);
    const ids = view.edges.map((e) => e.id);
    expect(ids).toContain("band:group:g0>g1.s0");
    expect(ids).toContain("band:g1.s0>group:g2");
    expect(ids).toContain("g1.s0->g1.s1:http");
    // Open and closed report the same numbers.
    const closed = buildLevelView(graph).nodes.find((n) => n.id === container.id) as DrawnGroupNode;
    expect([container.linksIn, container.linksOut, container.linksInside]).toEqual([
      closed.linksIn,
      closed.linksOut,
      closed.linksInside,
    ]);
  });

  it("opens a group over 40 services to its 30 most-linked plus +N more", () => {
    const big = groupedEstate(2, 45);
    const view = buildLevelView(big, { open: ["g0"] });
    const children = ofType(view.nodes, "service").filter((n) => n.parentId === groupNodeId("g0"));
    expect(children).toHaveLength(30);
    const more = view.nodes.find((n) => n.id === "more:group:g0") as DrawnMoreNode;
    expect(more.serviceIds).toHaveLength(15);
    expect(view.truncation?.foldedServices).toBe(15);
    // Edges to folded services land on the "+N more" node.
    expect(view.edges.some((e) => e.target === "more:group:g0" || e.source === "more:group:g0")).toBe(true);
  });

  it("keeps a grouped member with no runtime link in its group, not the tray", () => {
    const g = groupedEstate(3, 20);
    const nodes = [...g.nodes, { id: "g2.idle", name: "g2.idle", repo: "g2.idle", group: "g2" }];
    const view = buildLevelView({ ...g, nodes });
    expect(view.nodes.some((n) => n.type === "tray")).toBe(false);
    const g2 = view.nodes.find((n) => n.id === groupNodeId("g2")) as DrawnGroupNode;
    expect(g2.serviceIds).toContain("g2.idle");
  });
});

describe("draw budget", () => {
  it("folds a flat estate over budget into +N more and says so", () => {
    const view = buildLevelView(flatRing(300));
    expect(view.nodes).toHaveLength(120);
    const more = view.nodes.at(-1) as DrawnMoreNode;
    expect(more.id).toBe(ESTATE_MORE_ID);
    expect(more.serviceIds).toHaveLength(181);
    expect(view.truncation).toMatchObject({ foldedServices: 181, foldedGroups: 0 });
    // Edges into the folded services become bands on the "+N more" node.
    expect(view.edges.some((e) => e.type === "band" && (e.source === ESTATE_MORE_ID || e.target === ESTATE_MORE_ID))).toBe(true);
  });

  it("drops the weakest connectors past the edge budget and counts them", () => {
    const view = buildLevelView(flatRing(100), {}, { nodes: 120, edges: 40 });
    expect(view.edges).toHaveLength(40);
    expect(view.truncation?.droppedEdges).toBe(60);
    expect(view.truncation?.droppedLinks).toBeGreaterThan(0);
  });

  it("folds the weakest group cards when there are more groups than room", () => {
    const view = buildLevelView(groupedEstate(30, 2), {}, { nodes: 10, edges: 300 });
    expect(view.nodes).toHaveLength(10);
    const more = view.nodes.find((n) => n.id === ESTATE_MORE_ID) as DrawnMoreNode;
    expect(more.groupIds).toHaveLength(21);
    expect(view.truncation?.foldedGroups).toBe(21);
  });

  it("never folds a small open group to make room for the estate", () => {
    const g = groupedEstate(3, 20, flatRing(200).edges);
    const graph = { ...g, nodes: [...g.nodes, ...flatRing(200).nodes] };
    const view = buildLevelView(graph, { open: ["g0"] });
    expect(view.nodes.length).toBeLessThanOrEqual(120);
    expect(ofType(view.nodes, "service").filter((n) => n.parentId === groupNodeId("g0"))).toHaveLength(20);
  });
});

describe("level precedence and determinism", () => {
  it("between wins over focus, focus over open, unknown ids fall through", () => {
    expect(buildLevelView(ESHOP_GRAPH, { between: { a: "webapp", b: "ordering.api" }, focus: "webapp" }).level).toBe("between");
    expect(buildLevelView(ESHOP_GRAPH, { between: { a: "webapp", b: "missing" }, focus: "webapp" }).level).toBe("focus");
    expect(buildLevelView(ESHOP_GRAPH, { focus: "missing" }).level).toBe("estate");
  });

  it("draws the between level as the pair plus the services on its paths", () => {
    const view = buildLevelView(ESHOP_GRAPH, { between: { a: "webhookclient", b: "catalog.api" } });
    expect(view.nodes.map((n) => n.id)).toEqual(["catalog.api", "webhookclient", "webhooks.api"]);
    expect(view.edges.map((e) => e.id)).toEqual([
      "webhookclient->webhooks.api:http",
      "webhooks.api->catalog.api:event",
    ]);
    expect(view.between?.pathsAToB[0]?.nodes).toEqual(["webhookclient", "webhooks.api", "catalog.api"]);
  });

  it("returns the same view whatever order the payload arrives in", () => {
    const shuffled: LevelsGraph = {
      nodes: [...ESHOP_GRAPH.nodes].reverse(),
      edges: [...ESHOP_GRAPH.edges].reverse(),
    };
    for (const state of [
      {},
      { families: new Set<EdgeFamily>(["runtime", "build", "history", "data"]) },
      { focus: "ordering.api", hops: 2 as const },
      { between: { a: "webapp", b: "ordering.api" } },
    ]) {
      expect(buildLevelView(shuffled, state)).toEqual(buildLevelView(ESHOP_GRAPH, state));
    }
    const big = groupedEstate(5, 30);
    const bigShuffled = { ...big, nodes: [...big.nodes].reverse(), edges: [...big.edges].reverse() };
    expect(buildLevelView(bigShuffled, { open: ["g2"] })).toEqual(buildLevelView(big, { open: ["g2"] }));
  });

  it("drops dangling edges and duplicate ids instead of drawing phantoms", () => {
    const dirty: LevelsGraph = {
      nodes: [...ESHOP_GRAPH.nodes, ESHOP_GRAPH.nodes[0]!],
      edges: [...ESHOP_GRAPH.edges, ESHOP_GRAPH.edges[2]!, { ...ESHOP_GRAPH.edges[2]!, id: "ghost", target: "gone" }],
    };
    expect(buildLevelView(dirty)).toEqual(buildLevelView(ESHOP_GRAPH));
    const focus = buildLevelView(dirty, { focus: "basket.api" });
    expect(focus.nodes.some((n) => n.type === "service" && n.serviceId === "gone")).toBe(false);
  });

  it("picks the same survivor of a duplicate id whatever the payload order", () => {
    const a = { id: "x", name: "x", repo: "repo-a" };
    const b = { id: "x", name: "x", repo: "repo-b" };
    const heavy = { ...ESHOP_GRAPH.edges[0]!, weight: 9 };
    const g1: LevelsGraph = { nodes: [...ESHOP_GRAPH.nodes, a, b], edges: [...ESHOP_GRAPH.edges, heavy] };
    const g2: LevelsGraph = { nodes: [b, ...ESHOP_GRAPH.nodes, a], edges: [heavy, ...ESHOP_GRAPH.edges] };
    expect(normalizeGraph(g1)).toEqual(normalizeGraph(g2));
    expect(normalizeGraph(g1).nodes.filter((n) => n.id === "x")).toEqual([a]);
  });

  it("normalizes a payload once and reuses it", () => {
    const g: LevelsGraph = { nodes: [...ESHOP_GRAPH.nodes], edges: [...ESHOP_GRAPH.edges] };
    const first = normalizeGraph(g);
    expect(normalizeGraph(g)).toBe(first);
    expect(normalizeGraph(first)).toBe(first);
  });

  it("gives every drawn node and edge a unique id at every level", () => {
    const g = groupedEstate(3, 20);
    for (const view of [
      buildLevelView(flatRing(300)),
      buildLevelView(g, { open: ["g1"] }),
      buildLevelView(ESHOP_GRAPH, { focus: "ordering.api", hops: 2 }),
      buildLevelView(ESHOP_GRAPH, { openTray: true }),
    ]) {
      const ids = [...view.nodes, ...view.edges].map((x) => x.id);
      expect(new Set(ids).size).toBe(ids.length);
    }
  });

  it("counts a direct Data link between two services under the family filter", () => {
    const g: LevelsGraph = {
      nodes: ["a", "b"].map((id) => ({ id, name: id, repo: id })),
      edges: [edge("a", "b", "db", 2)],
    };
    const view = buildLevelView(g, { between: { a: "a", b: "b" } });
    expect(view.between?.aToB?.kinds).toEqual({ db: 2 });
    expect(view.edges.map((e) => e.id)).toEqual(["a->b:db"]);
  });

  it("reports the families drawn", () => {
    expect(buildLevelView(ESHOP_GRAPH).families).toBe(DEFAULT_EDGE_FAMILIES);
  });
});
