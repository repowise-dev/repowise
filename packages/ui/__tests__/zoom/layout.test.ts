import { describe, it, expect } from "vitest";
import type { Rect } from "../../src/zoom/camera";
import { CARD_ASPECT } from "../../src/zoom/constants";
import { labelAspect } from "../../src/zoom/card-content";
import {
  gridDimensions,
  type LayoutChild,
  type LayoutRelation,
  packLayout,
} from "../../src/zoom/layout";

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

  it("widens a card whose name needs room, keeping importance as its height", () => {
    const kids: LayoutChild[] = [
      { ...child("short", 1, 0.5), name: "Core", kind: "layer" },
      { ...child("long", 2, 0.5), name: "Command Line Interface", kind: "layer" },
    ];
    const r = packLayout(kids, 1);
    const short = r.get("short")!;
    const long = r.get("long")!;
    expect(short.w / short.h).toBeCloseTo(CARD_ASPECT, 5);
    expect(long.w / long.h).toBeCloseTo(labelAspect("Command Line Interface", "layer"), 5);
    expect(long.h).toBeCloseTo(short.h, 9);
  });

  it("keeps the name-aware aspect depth-stable", () => {
    const kids: LayoutChild[] = Array.from({ length: 5 }, (_, i) => ({
      ...child(`n${i}`, i + 1, (i + 1) / 5),
      name: "Infrastructure Provisioning",
      kind: "group" as const,
    }));
    const want = labelAspect("Infrastructure Provisioning", "group");
    for (const parentAspect of [1, 1.5, 3]) {
      for (const r of packLayout(kids, parentAspect).values()) {
        expect((r.w * parentAspect) / r.h).toBeCloseTo(want, 5);
      }
    }
  });

  it("packs to the parent's shape: a wide parent gets fewer, longer rows", () => {
    const kids = Array.from({ length: 12 }, (_, i) => child(`n${i}`, i + 1, ((i * 5) % 12) / 12));
    const rowsIn = (aspect: number): number =>
      new Set([...packLayout(kids, aspect).values()].map((r) => Math.round((r.y + r.h / 2) * 1e6))).size;
    expect(rowsIn(1.75)).toBeLessThan(rowsIn(1));
  });

  it("fills the parent: the cluster spans the binding axis and most of the other", () => {
    const kids = Array.from({ length: 11 }, (_, i) => child(`n${i}`, i + 1, ((i * 7) % 11) / 11));
    for (const aspect of [1, 1.75, 2.4]) {
      const rects = [...packLayout(kids, aspect).values()];
      const spanX = Math.max(...rects.map((r) => r.x + r.w)) - Math.min(...rects.map((r) => r.x));
      const spanY = Math.max(...rects.map((r) => r.y + r.h)) - Math.min(...rects.map((r) => r.y));
      expect(Math.max(spanX, spanY)).toBeCloseTo(1, 6);
      expect(Math.min(spanX, spanY)).toBeGreaterThan(0.7);
    }
  });
});

const link = (source_id: string, target_id: string, edge_count = 1): LayoutRelation => ({
  source_id,
  target_id,
  edge_count,
});

const midY = (r: Rect): number => r.y + r.h / 2;
const midX = (r: Rect): number => r.x + r.w / 2;

/** The packed rows, top to bottom, each as ids left to right. */
function rowsOf(rects: Map<string, Rect>): string[][] {
  const byY = new Map<number, [string, Rect][]>();
  for (const [id, r] of rects) {
    const k = Math.round(midY(r) * 1e6);
    byY.set(k, [...(byY.get(k) ?? []), [id, r]]);
  }
  return [...byY.entries()]
    .sort((a, b) => a[0] - b[0])
    .map(([, row]) => row.sort((a, b) => a[1].x - b[1].x).map(([id]) => id));
}

describe("packLayout with sibling relations", () => {
  it("ranks callers above callees, whatever the importance order", () => {
    // A chain a -> b -> c -> d -> e -> f, ranked the other way round.
    const ids = ["a", "b", "c", "d", "e", "f"];
    const kids = ids.map((id, i) => child(id, ids.length - i, 0.5));
    const rels = ids.slice(1).map((id, i) => link(ids[i]!, id));
    const r = packLayout(kids, 1, {}, rels);
    for (const rel of rels) {
      expect(midY(r.get(rel.source_id)!)).toBeLessThanOrEqual(midY(r.get(rel.target_id)!) + 1e-9);
    }
    expect(midY(r.get("a")!)).toBeLessThan(midY(r.get("f")!));
  });

  it("breaks a cycle at its weakest link", () => {
    const kids = [child("c", 1), child("b", 2), child("a", 3)];
    const rels = [link("a", "b", 10), link("b", "c", 10), link("c", "a", 1)];
    // A tall parent: one card per row, so the order reads top to bottom.
    const r = packLayout(kids, 0.3, {}, rels);
    expect(midY(r.get("a")!)).toBeLessThan(midY(r.get("b")!));
    expect(midY(r.get("b")!)).toBeLessThan(midY(r.get("c")!));
  });

  it("orders a row to cut crossings: a callee sits under its caller", () => {
    const kids = [child("p", 1), child("q", 2), child("x", 3), child("y", 4)];
    const rels = [link("p", "y", 5), link("q", "x", 5)];
    const r = packLayout(kids, CARD_ASPECT, {}, rels);
    const rows = rowsOf(r);
    expect(rows).toHaveLength(2);
    expect(new Set(rows[0])).toEqual(new Set(["p", "q"]));
    expect(midX(r.get("p")!) < midX(r.get("q")!)).toBe(midX(r.get("y")!) < midX(r.get("x")!));
  });

  it("keeps importance driving size and the rank order where links do not decide", () => {
    const kids = [child("hi", 1, 0.9), child("lo", 2, 0.1), child("x", 3, 0.5), child("y", 4, 0.5)];
    const r = packLayout(kids, 1, {}, [link("x", "y")]);
    expect(area(r.get("hi")!)).toBeGreaterThan(area(r.get("lo")!));
    // Linked children rank first; the unlinked follow in rank order.
    expect(midY(r.get("x")!)).toBeLessThanOrEqual(midY(r.get("hi")!) + 1e-9);
  });

  it("ignores links to children outside the set and self links", () => {
    const kids = Array.from({ length: 5 }, (_, i) => child(`n${i}`, i + 1, (i + 1) / 5));
    const plain = packLayout(kids, 1.4);
    const noisy = packLayout(kids, 1.4, {}, [link("n0", "gone", 9), link("n1", "n1", 9)]);
    expect([...noisy.entries()]).toEqual([...plain.entries()]);
  });

  it("stays deterministic, inside the box and overlap-free with links", () => {
    const kids = Array.from({ length: 14 }, (_, i) => child(`n${i}`, i + 1, ((i * 5) % 14) / 14));
    const rels = Array.from({ length: 30 }, (_, i) => link(`n${(i * 3) % 14}`, `n${(i * 5 + 1) % 14}`, 1 + (i % 7)));
    const a = packLayout(kids, 1.7, {}, rels);
    expect([...packLayout(kids, 1.7, {}, rels).entries()]).toEqual([...a.entries()]);
    const rects = [...a.values()];
    for (const r of rects) {
      expect(r.x).toBeGreaterThanOrEqual(-1e-9);
      expect(r.x + r.w).toBeLessThanOrEqual(1 + 1e-9);
      expect(r.y + r.h).toBeLessThanOrEqual(1 + 1e-9);
    }
    for (let i = 0; i < rects.length; i++) {
      for (let j = i + 1; j < rects.length; j++) expect(overlaps(rects[i]!, rects[j]!)).toBe(false);
    }
  });
});

describe("setting children aside", () => {
  const aside = new Set(["tests"]);

  it("puts them in a column on the right and ranks without their links", () => {
    const kids = [child("tests", 1, 1), child("a", 2), child("b", 3), child("c", 4)];
    const rels = [
      link("tests", "a", 100),
      link("tests", "b", 100),
      link("b", "tests", 100),
      link("a", "b", 3),
      link("b", "c", 3),
    ];
    const parentAspect = 1.8;
    const r = packLayout(kids, parentAspect, {}, rels, aside);
    const test = r.get("tests")!;
    for (const id of ["a", "b", "c"]) {
      expect(r.get(id)!.x + r.get(id)!.w).toBeLessThan(test.x);
    }
    expect(test.x + test.w).toBeLessThanOrEqual(1 + 1e-9);
    expect(test.y).toBeCloseTo(Math.min(...["a", "b", "c"].map((id) => r.get(id)!.y)), 9);
    expect(midY(r.get("a")!)).toBeLessThanOrEqual(midY(r.get("b")!) + 1e-9);
    expect(midY(r.get("b")!)).toBeLessThanOrEqual(midY(r.get("c")!) + 1e-9);
    // Both columns keep the on-screen card aspect once composeRect stretches them.
    for (const rect of r.values()) expect((rect.w * parentAspect) / rect.h).toBeCloseTo(CARD_ASPECT, 5);
  });

  it("packs a lone set-aside child like any other", () => {
    expect([...packLayout([child("tests", 1)], 1.5, {}, [], aside).entries()]).toEqual([
      ...packLayout([child("tests", 1)], 1.5).entries(),
    ]);
  });
});
