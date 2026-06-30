import { describe, it, expect } from "vitest";
import type { Rect } from "../../src/zoom/camera";
import { gridDimensions, gridLayout, type LayoutChild } from "../../src/zoom/layout";

function child(id: string, rank: number, importance = 0.5): LayoutChild {
  return { id, sibling_rank: rank, importance };
}

/** Do two rects overlap (strictly, ignoring shared edges)? */
function overlaps(a: Rect, b: Rect): boolean {
  return a.x < b.x + b.w && b.x < a.x + a.w && a.y < b.y + b.h && b.y < a.y + a.h;
}

describe("gridDimensions", () => {
  it("is a single cell for one child", () => {
    expect(gridDimensions(1, 1)).toEqual({ cols: 1, rows: 1 });
  });
  it("is square-ish for a square parent", () => {
    expect(gridDimensions(4, 1)).toEqual({ cols: 2, rows: 2 });
    expect(gridDimensions(9, 1)).toEqual({ cols: 3, rows: 3 });
  });
  it("stacks vertically in a tall parent", () => {
    expect(gridDimensions(2, 0.25)).toEqual({ cols: 1, rows: 2 });
  });
  it("spreads horizontally in a wide parent", () => {
    expect(gridDimensions(2, 4)).toEqual({ cols: 2, rows: 1 });
  });
  it("is empty for zero children", () => {
    expect(gridDimensions(0, 1)).toEqual({ cols: 0, rows: 0 });
  });
});

describe("gridLayout", () => {
  const square = { gutter: 0, importanceScaleMin: 1 };

  it("tiles a 2x2 grid with no gutter or importance scaling", () => {
    const kids = [child("a", 1), child("b", 2), child("c", 3), child("d", 4)];
    const r = gridLayout(kids, 1, square);
    expect(r.get("a")).toEqual({ x: 0, y: 0, w: 0.5, h: 0.5 });
    expect(r.get("b")).toEqual({ x: 0.5, y: 0, w: 0.5, h: 0.5 });
    expect(r.get("c")).toEqual({ x: 0, y: 0.5, w: 0.5, h: 0.5 });
    expect(r.get("d")).toEqual({ x: 0.5, y: 0.5, w: 0.5, h: 0.5 });
  });

  it("places the most important child top-left regardless of input order", () => {
    const kids = [child("low", 4), child("top", 1), child("mid", 2)];
    const r = gridLayout(kids, 1, square);
    const top = r.get("top")!;
    expect(top.x).toBe(0);
    expect(top.y).toBe(0);
  });

  it("centres a partial last row", () => {
    const kids = [child("a", 1), child("b", 2), child("c", 3)];
    const r = gridLayout(kids, 1, square); // 2x2 grid, 3 items
    const c = r.get("c")!; // the lone last-row item, centred across the two columns
    expect(c.x).toBeCloseTo(0.25, 9);
    expect(c.y).toBeCloseTo(0.5, 9);
  });

  it("applies a per-axis gutter", () => {
    const r = gridLayout([child("a", 1, 1)], 1, { gutter: 0.1, importanceScaleMin: 1 });
    expect(r.get("a")).toEqual({ x: 0.1, y: 0.1, w: 0.8, h: 0.8 });
  });

  it("shrinks a low-importance box toward its cell centre", () => {
    const r = gridLayout([child("a", 1, 0)], 1, { gutter: 0, importanceScaleMin: 0.5 });
    // importance 0 -> scale 0.5 of the full cell, centred
    expect(r.get("a")).toEqual({ x: 0.25, y: 0.25, w: 0.5, h: 0.5 });
  });

  it("never overlaps siblings and is deterministic", () => {
    const kids = Array.from({ length: 17 }, (_, i) => child(`n${i}`, i + 1, Math.random()));
    const a = gridLayout(kids, 1.3);
    const b = gridLayout(kids, 1.3);
    expect([...a.entries()]).toEqual([...b.entries()]);
    const rects = [...a.values()];
    for (let i = 0; i < rects.length; i++) {
      for (let j = i + 1; j < rects.length; j++) {
        expect(overlaps(rects[i]!, rects[j]!)).toBe(false);
      }
    }
  });

  it("keeps every box inside the unit parent box", () => {
    const kids = Array.from({ length: 11 }, (_, i) => child(`n${i}`, i + 1, 1));
    for (const r of gridLayout(kids, 2).values()) {
      expect(r.x).toBeGreaterThanOrEqual(-1e-9);
      expect(r.y).toBeGreaterThanOrEqual(-1e-9);
      expect(r.x + r.w).toBeLessThanOrEqual(1 + 1e-9);
      expect(r.y + r.h).toBeLessThanOrEqual(1 + 1e-9);
    }
  });

  it("is empty for no children", () => {
    expect(gridLayout([], 1).size).toBe(0);
  });
});
