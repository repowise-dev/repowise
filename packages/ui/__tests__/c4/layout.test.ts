import { describe, it, expect } from "vitest";
import { computeC4Layout } from "../../src/c4/layout/elk-c4-layout";

describe("computeC4Layout", () => {
  it("returns empty map for empty input", async () => {
    const positions = await computeC4Layout([], []);
    expect(positions.size).toBe(0);
  });

  it("positions every input node", async () => {
    const nodes = [
      { id: "person:user", width: 140, height: 90 },
      { id: "sys:demo", width: 220, height: 110 },
      { id: "ext:fastapi", width: 180, height: 90 },
    ];
    const edges = [
      { id: "e1", source: "person:user", target: "sys:demo" },
      { id: "e2", source: "sys:demo", target: "ext:fastapi" },
    ];
    const positions = await computeC4Layout(nodes, edges);
    expect(positions.size).toBe(3);
    for (const n of nodes) {
      const p = positions.get(n.id);
      expect(p).toBeDefined();
      expect(p!.width).toBe(n.width);
      expect(p!.height).toBe(n.height);
    }
  });

  it("drops edges with unknown endpoints and self-loops", async () => {
    const nodes = [{ id: "a", width: 100, height: 50 }];
    const edges = [
      { id: "e1", source: "a", target: "ghost" },
      { id: "e2", source: "a", target: "a" },
    ];
    const positions = await computeC4Layout(nodes, edges);
    expect(positions.size).toBe(1);
    expect(positions.has("a")).toBe(true);
  });

  it("lays out edges top-down (target below source)", async () => {
    const nodes = [
      { id: "top", width: 100, height: 50 },
      { id: "bottom", width: 100, height: 50 },
    ];
    const positions = await computeC4Layout(nodes, [
      { id: "e1", source: "top", target: "bottom" },
    ]);
    const top = positions.get("top")!;
    const bottom = positions.get("bottom")!;
    expect(bottom.y).toBeGreaterThan(top.y);
  });
});
