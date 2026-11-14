import { describe, expect, it } from "vitest";
import { allocateSlots, capEdges, truncationReport, DRAW_BUDGET } from "../../src/workspace/system-map/draw-budget";
import { connectEdges, summarizeLinks } from "../../src/workspace/system-map/bands";
import {
  DEFAULT_EDGE_FAMILIES,
  EDGE_FAMILY_ORDER,
  edgeFamily,
  familyCounts,
  familyTally,
  linkWeight,
} from "../../src/workspace/system-map/edge-families";
import { EDGE_KIND_ORDER, SYSTEM_EDGE_KINDS } from "../../src/workspace/system-map/edge-kinds";
import type { DrawnEdge, LevelsEdge } from "../../src/workspace/system-map/level-types";

function edge(source: string, target: string, kind: LevelsEdge["kind"] = "http", weight = 1): LevelsEdge {
  return { id: `${source}->${target}:${kind}`, source, target, kind, weight, strategy: null };
}

describe("edge families", () => {
  it("puts every registered kind in a family, Runtime and Data on by default", () => {
    for (const kind of EDGE_KIND_ORDER) expect(EDGE_FAMILY_ORDER).toContain(SYSTEM_EDGE_KINDS[kind].family);
    expect(["http", "grpc", "socket", "event"].map(edgeFamily)).toEqual(["runtime", "runtime", "runtime", "runtime"]);
    expect([edgeFamily("db"), edgeFamily("package"), edgeFamily("co_change")]).toEqual(["data", "build", "history"]);
    expect([...DEFAULT_EDGE_FAMILIES]).toEqual(["runtime", "data"]);
  });

  it("files an unknown kind under History, as core does", () => {
    expect(edgeFamily("graphql")).toBe("history");
  });

  it("counts edges and links per family present", () => {
    const counts = familyCounts([edge("a", "b", "http", 3), edge("a", "c", "package"), edge("b", "c", "event", 0)]);
    expect(counts).toEqual({ runtime: { edges: 2, links: 4 }, build: { edges: 1, links: 1 } });
    expect(linkWeight({ weight: 0 })).toBe(1);
  });

  it("tallies present families in chip order, drawn or hidden by the filter", () => {
    const edges = [edge("a", "b", "co_change"), edge("a", "c", "package", 4), edge("b", "c", "http"), edge("c", "d", "event")];
    expect(familyTally(edges, DEFAULT_EDGE_FAMILIES)).toEqual([
      { family: "runtime", edges: 2, drawn: true },
      { family: "build", edges: 1, drawn: false },
      { family: "history", edges: 1, drawn: false },
    ]);
    expect(familyTally([], DEFAULT_EDGE_FAMILIES)).toEqual([]);
  });
});

describe("allocateSlots", () => {
  it("keeps everything when it fits", () => {
    expect(allocateSlots([{ size: 10 }, { size: 5 }], 15)).toEqual([10, 5]);
  });

  it("caps the big container first and leaves room for its +N more", () => {
    // 300 + 35 into 119: the open group keeps all 35, the root keeps 83 plus one "+N more".
    expect(allocateSlots([{ size: 300 }, { size: 35 }], 119)).toEqual([83, 35]);
  });

  it("honours a container's own limit", () => {
    expect(allocateSlots([{ size: 45, limit: 30 }, { size: 3 }], 119)).toEqual([30, 3]);
  });

  it("hands slots the shared cap leaves over to containers still folding", () => {
    // Cap 4 fits (4 + 4 + 2 = 10); the one spare slot goes to the first container.
    expect(allocateSlots([{ size: 10 }, { size: 10 }], 11)).toEqual([5, 4]);
    // The last slot of a container that then fits whole also frees its "+N more".
    expect(allocateSlots([{ size: 3 }, { size: 10 }], 8)).toEqual([3, 4]);
  });

  it("keeps nothing when even one +N more per container does not fit", () => {
    expect(allocateSlots([{ size: 5 }, { size: 5 }], 1)).toEqual([0, 0]);
    expect(allocateSlots([], 10)).toEqual([]);
  });

  it("never exceeds capacity when it can be met", () => {
    for (const capacity of [3, 10, 50, 119]) {
      const sizes = [{ size: 80 }, { size: 7 }, { size: 41, limit: 30 }, { size: 0 }];
      const kept = allocateSlots(sizes, capacity);
      const cost = kept.reduce((s, k, i) => s + k + (k < sizes[i]!.size ? 1 : 0), 0);
      expect(cost).toBeLessThanOrEqual(capacity);
    }
  });
});

describe("capEdges", () => {
  const edges = [edge("a", "b", "http", 1), edge("b", "c", "http", 5), edge("c", "d", "http", 3)];
  const byId = new Map(edges.map((e) => [e.id, e]));
  const drawn: DrawnEdge[] = edges.map((e) => ({ type: "edge", id: e.id, source: e.source, target: e.target, edgeId: e.id }));

  it("keeps the strongest, in their original order, and counts the rest", () => {
    const r = capEdges(drawn, 2, byId);
    expect(r.edges.map((e) => e.id)).toEqual(["b->c:http", "c->d:http"]);
    expect(r).toMatchObject({ droppedEdges: 1, droppedLinks: 1 });
  });

  it("is a no-op under budget", () => {
    expect(capEdges(drawn, DRAW_BUDGET.edges, byId)).toMatchObject({ droppedEdges: 0, droppedLinks: 0 });
  });

  it("reports nothing when nothing was cut", () => {
    expect(truncationReport(DRAW_BUDGET, 0, 0, 0, 0)).toBeNull();
    expect(truncationReport(DRAW_BUDGET, 2, 0, 0, 0)).toMatchObject({ foldedServices: 2 });
  });
});

describe("connectEdges", () => {
  const where: Record<string, string> = { a: "a", b: "b", c: "group:g", d: "group:g", e: "tray:" };
  const cards = new Set(["a", "b"]);
  const run = (edges: LevelsEdge[]) => connectEdges(edges, (id) => where[id] ?? null, (id) => cards.has(id));

  it("keeps card-to-card edges single and bands the rest per ordered pair", () => {
    const edges = run([
      edge("a", "b", "http"),
      edge("a", "b", "event", 2),
      edge("a", "c", "http", 2),
      edge("a", "d", "grpc"),
      edge("c", "a", "event"),
      edge("c", "d", "http", 4),
      edge("e", "z"),
    ]);
    expect(edges.map((e) => e.id)).toEqual([
      "a->b:event",
      "a->b:http",
      "band:a>group:g",
      "band:group:g>a",
    ]);
    const band = edges[2] as Extract<DrawnEdge, { type: "band" }>;
    expect(band.summary).toEqual({ links: 3, edges: 2, kinds: { http: 2, grpc: 1 }, gatewayLinks: 0, edgeIds: ["a->c:http", "a->d:grpc"] });
  });

  it("summarizes gateway-routed links", () => {
    const s = summarizeLinks([{ ...edge("a", "b"), strategy: "gateway_route" }, edge("a", "b", "event")]);
    expect(s).toMatchObject({ links: 2, gatewayLinks: 1 });
  });
});
