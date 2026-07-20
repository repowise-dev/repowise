import { describe, it, expect } from "vitest";
import type { Rect } from "../../src/zoom/camera";
import { CARD_ASPECT } from "../../src/zoom/constants";
import { gridDimensions, packLayout, type LayoutChild } from "../../src/zoom/layout";

function child(id: string, rank: number, importance = 0.5): LayoutChild {
  return { id, sibling_rank: rank, importance };
}

const area = (r: Rect): number => r.w * r.h;

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

describe("packLayout", () => {
  it("is empty for no children", () => {
    expect(packLayout([], 1).size).toBe(0);
  });

  it("sizes cards by importance (more important = larger)", () => {
    const kids = [child("hi", 1, 0.9), child("mid", 2, 0.5), child("lo", 3, 0.1)];
    const r = packLayout(kids, 1);
    expect(area(r.get("hi")!)).toBeGreaterThan(area(r.get("mid")!));
    expect(area(r.get("mid")!)).toBeGreaterThan(area(r.get("lo")!));
  });

  it("gives every card the same size when importance is uniform", () => {
    const kids = [child("a", 1, 0.5), child("b", 2, 0.5), child("c", 3, 0.5)];
    const r = packLayout(kids, 1);
    const w = r.get("a")!.w;
    expect(r.get("b")!.w).toBeCloseTo(w, 9);
    expect(r.get("c")!.w).toBeCloseTo(w, 9);
  });

  it("places the most important child in the top row and largest", () => {
    const kids = [child("low", 4, 0.1), child("top", 1, 0.9), child("mid", 2, 0.5)];
    const r = packLayout(kids, 1);
    const top = r.get("top")!;
    // Rows are centred (so left edges stagger), but the most important card is
    // always in the top row and the biggest.
    for (const id of ["low", "mid"]) {
      expect(top.y).toBeLessThanOrEqual(r.get(id)!.y + 1e-9);
      expect(area(top)).toBeGreaterThan(area(r.get(id)!));
    }
  });

  it("keeps every card a consistent landscape aspect", () => {
    const kids = Array.from({ length: 7 }, (_, i) => child(`n${i}`, i + 1, (i + 1) / 7));
    const ratios = [...packLayout(kids, 1.2).values()].map((r) => r.w / r.h);
    for (const ratio of ratios) expect(ratio).toBeCloseTo(ratios[0]!, 6);
  });

  it("cancels the parent stretch so on-screen aspect is depth-stable", () => {
    // Cards are sized in local space; composeRect later re-stretches width by the
    // parent's world width and height by its world height. Emulate a parent rect
    // of `{ w: parentAspect, h: 1 }` and confirm every card lands back at
    // CARD_ASPECT for a range of parent shapes, so nesting cannot compound the
    // stretch into an unreadable strip (the depth-7 sliver bug).
    const kids = Array.from({ length: 5 }, (_, i) => child(`n${i}`, i + 1, (i + 1) / 5));
    for (const parentAspect of [1, 1.5, 3, 6]) {
      for (const r of packLayout(kids, parentAspect).values()) {
        const onScreen = (r.w * parentAspect) / r.h; // composeRect re-applies parentAspect
        expect(onScreen).toBeCloseTo(CARD_ASPECT, 5);
      }
    }
  });

  it("never overlaps siblings and is deterministic", () => {
    const kids = Array.from({ length: 17 }, (_, i) => child(`n${i}`, i + 1, ((i * 7) % 17) / 17));
    const a = packLayout(kids, 1.3);
    const b = packLayout(kids, 1.3);
    expect([...a.entries()]).toEqual([...b.entries()]);
    const rects = [...a.values()];
    for (let i = 0; i < rects.length; i++) {
      for (let j = i + 1; j < rects.length; j++) {
        expect(overlaps(rects[i]!, rects[j]!)).toBe(false);
      }
    }
  });

  it("keeps every card inside the unit parent box", () => {
    const kids = Array.from({ length: 11 }, (_, i) => child(`n${i}`, i + 1, (i % 4) / 4));
    for (const r of packLayout(kids, 2).values()) {
      expect(r.x).toBeGreaterThanOrEqual(-1e-9);
      expect(r.y).toBeGreaterThanOrEqual(-1e-9);
      expect(r.x + r.w).toBeLessThanOrEqual(1 + 1e-9);
      expect(r.y + r.h).toBeLessThanOrEqual(1 + 1e-9);
    }
  });

  it("gives a lone child the full parent width", () => {
    const r = packLayout([child("only", 1, 0.5)], 1).get("only")!;
    expect(r.w).toBeCloseTo(1, 6);
    expect(r.x).toBeCloseTo(0, 6);
  });
});
