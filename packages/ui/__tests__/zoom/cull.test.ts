import { describe, it, expect } from "vitest";
import type { Viewport } from "../../src/zoom/camera";
import { isOnScreen, selectChildren } from "../../src/zoom/cull";
import type { ZoomNode } from "../../src/zoom/types";

const VP: Viewport = { w: 1000, h: 800 };

describe("isOnScreen", () => {
  it("keeps rects overlapping the viewport", () => {
    expect(isOnScreen({ x: 400, y: 300, w: 100, h: 100 }, VP, 0)).toBe(true);
    expect(isOnScreen({ x: -50, y: -50, w: 100, h: 100 }, VP, 0)).toBe(true);
  });
  it("drops fully off-screen rects", () => {
    expect(isOnScreen({ x: 2000, y: 300, w: 100, h: 100 }, VP, 0)).toBe(false);
    expect(isOnScreen({ x: 400, y: -500, w: 100, h: 100 }, VP, 0)).toBe(false);
  });
  it("honours the margin", () => {
    expect(isOnScreen({ x: -90, y: 300, w: 50, h: 50 }, VP, 64)).toBe(true);
    expect(isOnScreen({ x: -200, y: 300, w: 50, h: 50 }, VP, 64)).toBe(false);
  });
});

function leaf(id: string, rank: number): ZoomNode {
  return {
    id,
    parent_id: "p",
    level: 4,
    kind: "file",
    name: id,
    path: id,
    children: [],
    importance: 1 / rank,
    sibling_rank: rank,
    metrics: {
      file_count: 1,
      descendant_count: 0,
      hotspot_count: 0,
      dead_count: 0,
      entry_point_count: 0,
      on_flow_count: 0,
    },
    layout: { x: 0, y: 0, w: 1, h: 1 },
    summary: "",
    language: null,
    is_entry_point: false,
    is_hotspot: false,
    is_dead: false,
    is_test: false,
    on_flow: false,
    health_score: null,
  };
}

describe("selectChildren", () => {
  const children = [leaf("e", 5), leaf("a", 1), leaf("c", 3), leaf("b", 2), leaf("d", 4)];

  it("returns everything when under the cap", () => {
    expect(selectChildren(children, 10)).toHaveLength(5);
  });

  it("keeps the top-N by sibling_rank but preserves original order", () => {
    const kept = selectChildren(children, 3);
    expect(kept.map((c) => c.id)).toEqual(["a", "c", "b"]); // ranks 1,3,2 in input order
  });

  it("is deterministic on rank ties (breaks by id)", () => {
    const tied = [leaf("y", 1), leaf("x", 1), leaf("z", 1)];
    const kept = selectChildren(tied, 2);
    // ranked: x, y, z -> keep x,y -> input order y,x
    expect(kept.map((c) => c.id)).toEqual(["y", "x"]);
  });
});
