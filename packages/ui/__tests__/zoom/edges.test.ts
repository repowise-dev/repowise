import { describe, it, expect } from "vitest";
import type { Rect } from "../../src/zoom/camera";
import {
  controlPoints,
  type EdgeInput,
  facingSide,
  routeEdges,
  slotAnchor,
} from "../../src/zoom/edges";

const box = (x: number, y: number, w: number, h: number): Rect => ({ x, y, w, h });

describe("facingSide", () => {
  const r = box(0, 0, 100, 50); // centre (50, 25)
  it("faces the side toward the target", () => {
    expect(facingSide(r, { x: 500, y: 25 })).toBe("right");
    expect(facingSide(r, { x: -500, y: 25 })).toBe("left");
    expect(facingSide(r, { x: 50, y: -500 })).toBe("top");
    expect(facingSide(r, { x: 50, y: 500 })).toBe("bottom");
  });
  it("normalises by half-extents so a wide box still picks a horizontal side", () => {
    const wide = box(0, 0, 200, 20); // centre (100, 10)
    // dx=80 dy=8 -> 80/100=0.8 vs 8/10=0.8 tie -> horizontal wins on >=
    expect(facingSide(wide, { x: 180, y: 18 })).toBe("right");
  });
});

describe("slotAnchor", () => {
  const r = box(0, 0, 100, 100);
  it("centres a single slot on the side", () => {
    expect(slotAnchor(r, "top", 0, 1, 0.2)).toEqual({ x: 50, y: 0 });
    expect(slotAnchor(r, "right", 0, 1, 0.2)).toEqual({ x: 100, y: 50 });
    expect(slotAnchor(r, "bottom", 0, 1, 0.2)).toEqual({ x: 50, y: 100 });
    expect(slotAnchor(r, "left", 0, 1, 0.2)).toEqual({ x: 0, y: 50 });
  });
  it("fans multiple slots symmetrically around the side centre", () => {
    const gap = 0.2; // min(0.2*100, 100/4) = 20
    expect(slotAnchor(r, "top", 0, 3, gap).x).toBeCloseTo(30, 9);
    expect(slotAnchor(r, "top", 1, 3, gap).x).toBeCloseTo(50, 9);
    expect(slotAnchor(r, "top", 2, 3, gap).x).toBeCloseTo(70, 9);
  });
  it("clamps the gap so the fan never spills past the side", () => {
    // count 9 would want 0.2*100=20 each, but is clamped to 100/10 = 10
    const first = slotAnchor(r, "top", 0, 9, 0.2);
    const last = slotAnchor(r, "top", 8, 9, 0.2);
    expect(first.x).toBeGreaterThanOrEqual(0);
    expect(last.x).toBeLessThanOrEqual(100);
  });
});

describe("controlPoints", () => {
  it("projects each control point outward along its side normal", () => {
    const [c1, c2] = controlPoints({ x: 50, y: 0 }, "top", { x: 50, y: 100 }, "bottom", 0.5);
    expect(c1).toEqual({ x: 50, y: -50 }); // top normal (0,-1) * (100*0.5)
    expect(c2).toEqual({ x: 50, y: 150 }); // bottom normal (0,1) * 50
  });
});

describe("routeEdges", () => {
  it("routes a single edge from the facing sides of each box", () => {
    const rects = new Map([
      ["A", box(0, 0, 100, 100)],
      ["B", box(200, 0, 100, 100)],
    ]);
    const edges: EdgeInput[] = [
      { id: "A B", sourceId: "A", targetId: "B", coupling: "tight", edgeCount: 3 },
    ];
    const routed = routeEdges(edges, rects);
    expect(routed).toHaveLength(1);
    const { route } = routed[0]!;
    expect(route.start).toEqual({ x: 100, y: 50 }); // A right side
    expect(route.end).toEqual({ x: 200, y: 50 }); // B left side
    expect(route.endAngle).toBeCloseTo(0, 6); // arrow points right, into B
  });

  it("drops edges whose endpoints are not laid out", () => {
    const rects = new Map([["A", box(0, 0, 100, 100)]]);
    const edges: EdgeInput[] = [
      { id: "A Z", sourceId: "A", targetId: "Z", coupling: "loose", edgeCount: 1 },
    ];
    expect(routeEdges(edges, rects)).toHaveLength(0);
  });

  it("fans edges that share a box side into distinct slots", () => {
    const rects = new Map([
      ["A", box(0, 0, 100, 100)], // centre (50,50)
      ["B", box(200, -200, 100, 100)], // up-right of A
      ["C", box(200, 200, 100, 100)], // down-right of A
    ]);
    const edges: EdgeInput[] = [
      { id: "A B", sourceId: "A", targetId: "B", coupling: "loose", edgeCount: 1 },
      { id: "A C", sourceId: "A", targetId: "C", coupling: "loose", edgeCount: 1 },
    ];
    const routed = routeEdges(edges, rects);
    const ab = routed.find((r) => r.id === "A B")!;
    const ac = routed.find((r) => r.id === "A C")!;
    // Both leave A's right side, but on separate slots -> different start.y.
    expect(ab.route.start.x).toBe(100);
    expect(ac.route.start.x).toBe(100);
    expect(ab.route.start.y).not.toBeCloseTo(ac.route.start.y, 3);
  });

  it("is deterministic", () => {
    const rects = new Map([
      ["A", box(0, 0, 100, 100)],
      ["B", box(200, 0, 100, 100)],
      ["C", box(0, 200, 100, 100)],
    ]);
    const edges: EdgeInput[] = [
      { id: "A B", sourceId: "A", targetId: "B", coupling: "tight", edgeCount: 2 },
      { id: "A C", sourceId: "A", targetId: "C", coupling: "loose", edgeCount: 1 },
    ];
    expect(routeEdges(edges, rects)).toEqual(routeEdges(edges, rects));
  });
});
