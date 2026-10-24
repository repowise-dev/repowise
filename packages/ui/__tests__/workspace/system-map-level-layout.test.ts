import { describe, expect, it } from "vitest";
import { renderHook, waitFor } from "@testing-library/react";
import { buildLevelView } from "../../src/workspace/system-map/levels";
import { TRAY_NODE_ID, groupNodeId } from "../../src/workspace/system-map/estate-view";
import { placeFocusColumns } from "../../src/workspace/system-map/focus-columns";
import {
  CONTAINER_PADDING,
  LEVEL_NODE_SIZES,
  ROOT_SCOPE,
  fitContainer,
  PLACEMENT_EDGES_PER_CARD,
  orderByPrevious,
  placementEdges,
  planLevelLayout,
  planSignature,
} from "../../src/workspace/system-map/layout-scopes";
import { makeSyntheticSystemGraph } from "./fixtures/synthetic-system-graph";
import {
  computeLevelLayout,
  createLayoutCache,
  levelLayoutInput,
  type LayoutCache,
} from "../../src/workspace/system-map/level-layout";
import { useLevelLayout } from "../../src/workspace/system-map/use-level-layout";
import { DEFAULT_EDGE_FAMILIES, type EdgeFamily } from "../../src/workspace/system-map/edge-families";
import type { LevelsEdge, LevelsGraph } from "../../src/workspace/system-map/level-types";
import { ESHOP_GRAPH } from "./fixtures/eshop-system-graph";

function edge(source: string, target: string, kind: LevelsEdge["kind"] = "http"): LevelsEdge {
  return { id: `${source}->${target}:${kind}`, source, target, kind, weight: 1, strategy: null };
}

/** `groups` groups of `size` services; each group is a chain, and group i calls group i+1. */
function groupedEstate(groups: number, size: number): LevelsGraph {
  const nodes = [];
  const edges: LevelsEdge[] = [];
  for (let g = 0; g < groups; g++) {
    for (let s = 0; s < size; s++) {
      nodes.push({ id: `g${g}.s${s}`, name: `g${g}.s${s}`, repo: `g${g}.s${s}`, group: `g${g}` });
      if (s > 0) edges.push(edge(`g${g}.s${s - 1}`, `g${g}.s${s}`));
    }
    if (g + 1 < groups) edges.push(edge(`g${g}.s1`, `g${g + 1}.s0`, "event"));
  }
  // The model draws groups only when the payload lists them.
  return { nodes, edges, groups: Array.from({ length: groups }, (_, g) => ({ id: `g${g}` })) };
}

/** Counts cache traffic, so a test can tell a cache hit from an ELK run. */
function countingCache(): LayoutCache & { hits: number; misses: number } {
  const inner = createLayoutCache();
  const c = {
    hits: 0,
    misses: 0,
    get(key: string) {
      const v = inner.get(key);
      if (v) c.hits += 1;
      else c.misses += 1;
      return v;
    },
    set: inner.set,
    clear: inner.clear,
  };
  return c;
}

const all = new Set<EdgeFamily>(["runtime", "data", "build", "history"]);

describe("planLevelLayout", () => {
  it("lays a flat estate out as one root scope over every drawn card", () => {
    const view = buildLevelView(ESHOP_GRAPH);
    const plan = planLevelLayout(view.nodes, ESHOP_GRAPH.edges);
    expect(plan.interiors).toEqual([]);
    expect(plan.root.id).toBe(ROOT_SCOPE);
    expect(plan.root.nodes.map((n) => n.id)).toEqual(view.nodes.map((n) => n.id));
    // Build links into the closed tray still shape placement, lifted to the tray card.
    expect(plan.root.edges.some((e) => e.target === TRAY_NODE_ID)).toBe(true);
  });

  it("keys on every family but History, so a family toggle changes nothing", () => {
    const g: LevelsGraph = {
      nodes: ["a", "b", "c"].map((id) => ({ id, name: id, repo: id })),
      edges: [edge("a", "b"), edge("b", "c", "package"), edge("a", "c", "co_change")],
    };
    const runtimeOnly = buildLevelView(g);
    const everything = buildLevelView(g, { families: all });
    const plan = planLevelLayout(runtimeOnly.nodes, g.edges);
    expect(planSignature(plan)).toBe(planSignature(planLevelLayout(everything.nodes, g.edges)));
    // c has no runtime link, so it sits in the tray; co-change never shapes placement.
    expect(plan.root.edges.map((e) => e.id)).toEqual(["a>b", `b>${TRAY_NODE_ID}`]);
  });

  it("gives an open group its own scope and lifts cross-group edges to its box", () => {
    const g = groupedEstate(3, 15);
    const view = buildLevelView(g, { open: ["g1"] });
    const plan = planLevelLayout(view.nodes, g.edges);
    expect(plan.interiors.map((s) => s.id)).toEqual([groupNodeId("g1")]);
    const interior = plan.interiors[0]!;
    expect(interior.nodes).toHaveLength(15);
    expect(interior.edges).toHaveLength(14);
    expect(plan.root.nodes.map((n) => n.id).sort()).toEqual([groupNodeId("g0"), groupNodeId("g1"), groupNodeId("g2")]);
    // The open group's box is sized later, from its interior.
    expect(plan.root.nodes.find((n) => n.id === groupNodeId("g1"))!.width).toBe(0);
    expect(plan.root.edges.map((e) => e.id)).toEqual([
      `${groupNodeId("g0")}>${groupNodeId("g1")}`,
      `${groupNodeId("g1")}>${groupNodeId("g2")}`,
    ]);
  });
});

describe("placementEdges", () => {
  const w = (source: string, target: string, links: number) => [`${source}>${target}`, { source, target, links }] as const;

  it("keeps one edge per card pair, pointing the way most links go", () => {
    const edges = placementEdges(new Map([w("a", "b", 2), w("b", "a", 5), w("a", "c", 1)]), 3);
    expect(edges.map((e) => e.id)).toEqual(["a>c", "b>a"]);
  });

  it("keeps the strongest pairs, two per card, whatever order the weights came in", () => {
    const pairs = [w("a", "b", 9), w("c", "d", 1), w("a", "c", 4), w("b", "d", 4), w("a", "d", 2)];
    const forward = placementEdges(new Map(pairs), 2);
    expect(forward.map((e) => e.id)).toEqual(["a>b", "a>c", "a>d", "b>d"]);
    expect(placementEdges(new Map([...pairs].reverse()), 2)).toEqual(forward);
  });

  it("never leaves a linked card edgeless, however strong a hub's other pairs are", () => {
    const hub = ["x", "y", "z", "w"].flatMap((o) => [w("hub", o, 50), w(o, "hub", 40)]);
    const leaves = [w("leaf1", "leaf2", 1), w("leaf3", "x", 1)];
    const edges = placementEdges(new Map([...hub, ...leaves]), 2, 1);
    const ends = new Set(edges.flatMap((e) => [e.source, e.target]));
    for (const id of ["hub", "x", "y", "z", "w", "leaf1", "leaf2", "leaf3"]) expect(ends.has(id), id).toBe(true);
  });

  it("points a pair with equal links both ways from the lower id", () => {
    expect(placementEdges(new Map([w("b", "a", 3), w("a", "b", 3)]), 2).map((e) => e.id)).toEqual(["a>b"]);
  });

  it("caps a dense 300-service estate at two placement edges per card", () => {
    const g = makeSyntheticSystemGraph({ services: 300, edges: 3000, groups: 30, seed: 300 });
    const groups = [...new Set(g.nodes.map((n) => n.group!))].map((id) => ({ id }));
    const view = buildLevelView({ ...g, groups });
    const plan = planLevelLayout(view.nodes, g.edges);
    expect(plan.root.nodes).toHaveLength(30);
    expect(plan.root.edges.length).toBeLessThanOrEqual(30 * PLACEMENT_EDGES_PER_CARD);
    // One per pair: never both directions between two cards.
    const pairs = new Set(plan.root.edges.map((e) => [e.source, e.target].sort().join("|")));
    expect(pairs.size).toBe(plan.root.edges.length);
  });
});

describe("orderByPrevious", () => {
  const n = (id: string) => ({ id, width: 1, height: 1 });
  it("puts known nodes first in reading order, then new ones in drawn order", () => {
    const previous = new Map([
      ["b", { x: 0, y: 100 }],
      ["a", { x: 300, y: 0 }],
      ["c", { x: 0, y: 0 }],
    ]);
    expect(orderByPrevious([n("new2"), n("a"), n("b"), n("new1"), n("c")], previous).map((x) => x.id)).toEqual([
      "c",
      "a",
      "b",
      "new2",
      "new1",
    ]);
  });
});

describe("fitContainer", () => {
  it("shifts children into the padding and sizes the box around them", () => {
    const fitted = fitContainer(
      new Map([
        ["a", { x: 30, y: 30, width: 100, height: 50 }],
        ["b", { x: 230, y: 130, width: 100, height: 50 }],
      ]),
    );
    const pad = CONTAINER_PADDING;
    expect(fitted.children.get("a")).toEqual({ x: pad.left, y: pad.top, width: 100, height: 50 });
    expect(fitted.children.get("b")).toEqual({ x: pad.left + 200, y: pad.top + 100, width: 100, height: 50 });
    expect(fitted.width).toBe(300 + pad.left + pad.right);
    expect(fitted.height).toBe(150 + pad.top + pad.bottom);
  });
});

describe("computeLevelLayout", () => {
  it("places the focus level in fixed columns without ELK", async () => {
    const view = buildLevelView(ESHOP_GRAPH, { focus: "ordering.api" });
    const cache = countingCache();
    const positions = await computeLevelLayout(levelLayoutInput(view, ESHOP_GRAPH.edges), { cache });
    expect(positions).toEqual(placeFocusColumns(view.focus!, { size: LEVEL_NODE_SIZES.service }));
    expect(cache.hits + cache.misses).toBe(0);
  });

  it("positions every drawn card, children relative to their open group", async () => {
    const g = groupedEstate(3, 15);
    const view = buildLevelView(g, { open: ["g1"] });
    const positions = await computeLevelLayout(levelLayoutInput(view, g.edges));
    for (const n of view.nodes) expect(positions.has(n.id), n.id).toBe(true);
    const box = positions.get(groupNodeId("g1"))!;
    for (let s = 0; s < 15; s++) {
      const p = positions.get(`g1.s${s}`)!;
      expect(p.x).toBeGreaterThanOrEqual(CONTAINER_PADDING.left);
      expect(p.y).toBeGreaterThanOrEqual(CONTAINER_PADDING.top);
      expect(p.x + p.width).toBeLessThanOrEqual(box.width);
      expect(p.y + p.height).toBeLessThanOrEqual(box.height);
    }
    // Closed groups keep their card size.
    expect(positions.get(groupNodeId("g0"))!.width).toBe(LEVEL_NODE_SIZES.group.width);
  });

  it("serves a repeated view from the cache and lays out only what an opened group changed", async () => {
    const g = groupedEstate(3, 15);
    const cache = countingCache();
    const run = (open: string[]) => computeLevelLayout(levelLayoutInput(buildLevelView(g, { open }), g.edges), { cache });

    const first = await run(["g1"]);
    expect([cache.hits, cache.misses]).toEqual([0, 2]);
    expect(await run(["g1"])).toEqual(first);
    expect([cache.hits, cache.misses]).toEqual([2, 2]);

    // Opening a second group: g1's interior is reused; g2's and the root are new.
    await run(["g1", "g2"]);
    expect([cache.hits, cache.misses]).toEqual([3, 4]);
  });

  it("lays out the between level over the services on the paths", async () => {
    const view = buildLevelView(ESHOP_GRAPH, { between: { a: "webapp", b: "ordering.api" } });
    expect(view.level).toBe("between");
    const positions = await computeLevelLayout(levelLayoutInput(view, ESHOP_GRAPH.edges));
    expect([...positions.keys()].sort()).toEqual(view.nodes.map((n) => n.id).sort());
  });
});

describe("useLevelLayout", () => {
  it("lays out once and holds still when only the family filter moves", async () => {
    const g = groupedEstate(3, 15);
    const { result, rerender } = renderHook(({ families }) => {
      const view = buildLevelView(g, { open: ["g1"], families: families ?? DEFAULT_EDGE_FAMILIES });
      return useLevelLayout({ graph: g, view });
    }, { initialProps: { families: undefined as ReadonlySet<EdgeFamily> | undefined } });
    await waitFor(() => expect(result.current.loading).toBe(false));
    const first = result.current.positions;
    expect(first?.has("g1.s0")).toBe(true);
    rerender({ families: all });
    expect(result.current.loading).toBe(false);
    expect(result.current.positions).toBe(first);
  });
});
