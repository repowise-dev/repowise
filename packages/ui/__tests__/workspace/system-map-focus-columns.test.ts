import { describe, expect, it } from "vitest";
import {
  focusColumns,
  focusMoreId,
  focusNodeId,
  placeFocusColumns,
} from "../../src/workspace/system-map/focus-columns";
import { buildLevelView } from "../../src/workspace/system-map/levels";
import { DRAW_BUDGET } from "../../src/workspace/system-map/draw-budget";
import { DEFAULT_EDGE_FAMILIES, type EdgeFamily } from "../../src/workspace/system-map/edge-families";
import type { DrawnServiceNode, LevelsEdge, LevelsGraph } from "../../src/workspace/system-map/level-types";
import { ESHOP_GRAPH } from "./fixtures/eshop-system-graph";

const opts = { families: DEFAULT_EDGE_FAMILIES, budget: DRAW_BUDGET };

function star(callers: number): LevelsGraph {
  const nodes = [{ id: "hub", name: "hub", repo: "hub" }];
  const edges: LevelsEdge[] = [];
  for (let i = 0; i < callers; i++) {
    const id = `c${String(i).padStart(3, "0")}`;
    nodes.push({ id, name: id, repo: id });
    edges.push({ id: `${id}->hub`, source: id, target: "hub", kind: "http", weight: 1 + (i % 5), strategy: null });
  }
  return { nodes, edges };
}

describe("focusColumns on eShop ordering.api", () => {
  const layout = focusColumns(ESHOP_GRAPH, "ordering.api", opts)!;

  it("lists callers and dependencies by link count, ties by id", () => {
    const [callers, deps] = layout.columns;
    expect(callers!.id).toBe("callers");
    expect(callers!.entries.map((e) => [e.serviceId, e.summary.links])).toEqual([
      ["webapp", 8],
      ["clientapp", 4],
      ["catalog.api", 2],
      ["webhooks.api", 2],
      ["basket.api", 1],
      ["paymentprocessor", 1],
    ]);
    expect(deps!.entries.map((e) => [e.serviceId, e.summary.links])).toEqual([
      ["catalog.api", 2],
      ["paymentprocessor", 2],
      ["orderprocessor", 1],
    ]);
  });

  it("names the kind mix and marks a service on both sides", () => {
    const webapp = layout.columns[0]!.entries[0]!;
    expect(webapp.summary.kinds).toEqual({ event: 6, http: 2 });
    expect(webapp.bothWays).toBe(false);
    const both = layout.columns[0]!.entries.filter((e) => e.bothWays).map((e) => e.serviceId);
    expect(both).toEqual(["catalog.api", "paymentprocessor"]);
  });

  it("draws one connector per pair, into and out of the focus", () => {
    expect(layout.connectors).toHaveLength(9);
    const webapp = layout.connectors.find((c) => c.source === focusNodeId("callers", "webapp"))!;
    expect(webapp.target).toBe("ordering.api");
    expect(webapp.summary.edgeIds).toEqual(["webapp->ordering.api:event", "webapp->ordering.api:http"]);
    expect(layout.connectors.some((c) => c.source === "ordering.api" && c.target === focusNodeId("dependencies", "catalog.api"))).toBe(true);
  });

  it("follows the family filter", () => {
    const build = focusColumns(ESHOP_GRAPH, "ordering.api", { ...opts, families: new Set<EdgeFamily>(["build"]) })!;
    expect(build.columns[0]!.entries).toEqual([]);
    expect(build.columns[1]!.entries.map((e) => e.serviceId)).toEqual([
      "eshop.servicedefaults",
      "eventbusrabbitmq",
      "integrationeventlogef",
      "ordering.domain",
      "ordering.infrastructure",
    ]);
  });

  it("returns null for an unknown focus", () => {
    expect(focusColumns(ESHOP_GRAPH, "nope", opts)).toBeNull();
  });
});

describe("two hops", () => {
  const layout = focusColumns(ESHOP_GRAPH, "ordering.api", { ...opts, hops: 2 })!;

  it("adds callers of callers far left and dependencies of dependencies far right", () => {
    expect(layout.columns.map((c) => c.id)).toEqual(["callers2", "callers", "dependencies", "dependencies2"]);
    expect(layout.columns[0]!.entries.map((e) => [e.serviceId, e.summary.links])).toEqual([
      ["webappcomponents", 6],
      ["hybridapp", 5],
      ["webhookclient", 2],
    ]);
    expect(layout.columns[3]!.entries).toEqual([]);
  });

  it("connects the outer ring to the inner ring, never repeating a ring-one service", () => {
    const outer = layout.connectors.filter((c) => c.source.startsWith("callers2:"));
    expect(outer.map((c) => `${c.source}>${c.target}`)).toEqual([
      "callers2:hybridapp>callers:catalog.api",
      "callers2:webappcomponents>callers:catalog.api",
      "callers2:webhookclient>callers:webhooks.api",
    ]);
  });
});

describe("focus budget", () => {
  it("folds a column past the node budget into its +N more", () => {
    const layout = focusColumns(star(200), "hub", opts)!;
    const callers = layout.columns[0]!;
    expect(callers.entries).toHaveLength(118);
    expect(callers.folded).toHaveLength(82);
    expect(layout.connectors.some((c) => c.source === focusMoreId("callers"))).toBe(true);
    const view = buildLevelView(star(200), { focus: "hub" });
    expect(view.nodes).toHaveLength(120);
    expect(view.truncation?.foldedServices).toBe(82);
  });
});

describe("placeFocusColumns", () => {
  const size = { width: 200, height: 60 };

  it("places columns left to right, each centred on the focus row", () => {
    const layout = focusColumns(ESHOP_GRAPH, "ordering.api", opts)!;
    const pos = placeFocusColumns(layout, { size });
    expect(pos.get("ordering.api")).toEqual({ x: 296, y: -30, width: 200, height: 60 });
    expect(pos.get(focusNodeId("callers", "webapp"))).toEqual({ x: 0, y: -220, width: 200, height: 60 });
    expect(pos.get(focusNodeId("callers", "paymentprocessor"))!.y).toBe(160);
    expect(pos.get(focusNodeId("dependencies", "catalog.api"))!.x).toBe(592);
    expect(pos.size).toBe(10);
  });

  it("puts the +N more node last in its column and is deterministic", () => {
    const layout = focusColumns(star(200), "hub", opts)!;
    const a = placeFocusColumns(layout, { size });
    const b = placeFocusColumns(focusColumns(star(200), "hub", opts)!, { size });
    expect([...a]).toEqual([...b]);
    const more = a.get(focusMoreId("callers"))!;
    expect(Math.max(...[...a.values()].filter((p) => p.x === 0).map((p) => p.y))).toBe(more.y);
  });

  it("gives the level view the same ids the placement uses", () => {
    const view = buildLevelView(ESHOP_GRAPH, { focus: "ordering.api" });
    const pos = placeFocusColumns(view.focus!, { size });
    expect(view.nodes.every((n) => pos.has(n.id))).toBe(true);
    const focus = view.nodes.find((n) => n.id === "ordering.api") as DrawnServiceNode;
    expect(focus.column).toBe("focus");
  });
});
