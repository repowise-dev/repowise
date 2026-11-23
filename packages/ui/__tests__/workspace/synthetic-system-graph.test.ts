import { describe, it, expect } from "vitest";
import { buildLevelView } from "../../src/workspace/system-map/levels";
import { ESHOP_KIND_MIX, makeSyntheticSystemGraph } from "./fixtures/synthetic-system-graph";

describe("makeSyntheticSystemGraph", () => {
  it("is deterministic for a seed and differs across seeds", () => {
    const a = makeSyntheticSystemGraph({ services: 100, edges: 1000, groups: 10, seed: 7 });
    const b = makeSyntheticSystemGraph({ services: 100, edges: 1000, groups: 10, seed: 7 });
    const c = makeSyntheticSystemGraph({ services: 100, edges: 1000, groups: 10, seed: 8 });
    expect(b).toEqual(a);
    expect(c.edges.map((e) => e.id)).not.toEqual(a.edges.map((e) => e.id));
  });

  it("follows the eShop kind mix exactly, rounded", () => {
    const g = makeSyntheticSystemGraph({ services: 300, edges: 3000, groups: 30 });
    expect(g.edges).toHaveLength(3000);
    const counts = new Map<string, number>();
    for (const e of g.edges) counts.set(e.kind, (counts.get(e.kind) ?? 0) + 1);
    for (const [kind, share] of ESHOP_KIND_MIX) {
      expect(Math.abs((counts.get(kind) ?? 0) - 3000 * share)).toBeLessThanOrEqual(1);
    }
  });

  it("emits a well-formed graph: unique ids, known endpoints, no self loops", () => {
    const g = makeSyntheticSystemGraph({ services: 30, edges: 300, groups: 3, seed: 3 });
    const ids = new Set(g.nodes.map((n) => n.id));
    expect(ids.size).toBe(30);
    expect(new Set(g.edges.map((e) => e.id)).size).toBe(g.edges.length);
    for (const e of g.edges) {
      expect(ids.has(e.source) && ids.has(e.target)).toBe(true);
      expect(e.source).not.toBe(e.target);
      expect(e.id).toBe(`${e.source}->${e.target}:${e.kind}`);
      expect(e.contract_refs).toHaveLength(e.weight);
    }
    // Package edges land only on library repos, which concentrates them in hubs.
    expect(g.edges.filter((e) => e.kind === "package").every((e) => e.target.includes(".lib"))).toBe(true);
    // The levels model reads it like a real artifact.
    expect(buildLevelView(g).nodes.length).toBeGreaterThan(0);
  });

  it("tags groups only when asked for more than one", () => {
    const grouped = makeSyntheticSystemGraph({ services: 40, edges: 200, groups: 4 });
    expect(new Set(grouped.nodes.map((n) => n.group))).toEqual(new Set(["g0", "g1", "g2", "g3"]));
    const flat = makeSyntheticSystemGraph({ services: 18, edges: 44 });
    expect(flat.nodes.every((n) => !("group" in n))).toBe(true);
  });

  it("refuses an edge count the services cannot hold", () => {
    expect(() => makeSyntheticSystemGraph({ services: 4, edges: 400 })).toThrow(/cannot place/);
  });
});
