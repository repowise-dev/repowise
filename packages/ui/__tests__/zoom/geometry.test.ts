import { describe, it, expect } from "vitest";
import {
  composeRect,
  computeWorldRects,
  perimeterPoint,
  rectContains,
} from "../../src/zoom/geometry";
import type { ZoomNode } from "../../src/zoom/types";

function node(id: string, parent: string | null, children: string[], layout: ZoomNode["layout"]): ZoomNode {
  return {
    id,
    parent_id: parent,
    level: 0,
    kind: "folder",
    name: id,
    path: id,
    children,
    importance: 0.5,
    sibling_rank: 1,
    metrics: {
      file_count: 0,
      descendant_count: 0,
      hotspot_count: 0,
      dead_count: 0,
      entry_point_count: 0,
      on_flow_count: 0,
    },
    layout,
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

describe("composeRect", () => {
  it("nests a child's [0,1] rect inside its parent's absolute rect", () => {
    const parent = { x: 0.2, y: 0.4, w: 0.4, h: 0.2 };
    const child = composeRect(parent, { x: 0.5, y: 0, w: 0.5, h: 1 });
    expect(child).toEqual({ x: 0.4, y: 0.4, w: 0.2, h: 0.2 });
  });
});

describe("computeWorldRects", () => {
  it("lays children out within their parent's rect, recursively", () => {
    const nodes = new Map<string, ZoomNode>([
      ["root", node("root", null, ["a", "b"], null)],
      ["a", node("a", "root", ["a1"], null)],
      ["b", node("b", "root", [], null)],
      ["a1", node("a1", "a", [], null)],
    ]);
    const rects = computeWorldRects(nodes, "root");
    expect(rects.get("root")).toEqual({ x: 0, y: 0, w: 1, h: 1 });
    for (const id of ["a", "b", "a1"]) expect(rects.has(id)).toBe(true);

    const within = (child: string, parent: string) => {
      const c = rects.get(child)!;
      const p = rects.get(parent)!;
      const eps = 1e-9;
      expect(c.x).toBeGreaterThanOrEqual(p.x - eps);
      expect(c.y).toBeGreaterThanOrEqual(p.y - eps);
      expect(c.x + c.w).toBeLessThanOrEqual(p.x + p.w + eps);
      expect(c.y + c.h).toBeLessThanOrEqual(p.y + p.h + eps);
    };
    within("a", "root");
    within("b", "root");
    within("a1", "a"); // grid layout composes through every depth
  });

  it("lays out a node even when the backend gave it no layout rect", () => {
    const nodes = new Map<string, ZoomNode>([
      ["root", node("root", null, ["a", "ghost"], null)],
      ["a", node("a", "root", [], null)], // null backend layout -> still placed
      ["orphan", node("orphan", "nope", [], null)],
    ]);
    const rects = computeWorldRects(nodes, "root");
    expect(rects.has("a")).toBe(true); // layout is computed client-side now
    expect(rects.has("ghost")).toBe(false); // referenced but absent
    expect(rects.has("orphan")).toBe(false); // not reachable from root
  });

  it("returns empty when the root is missing", () => {
    expect(computeWorldRects(new Map(), "root").size).toBe(0);
  });
});

describe("perimeterPoint", () => {
  const r = { x: 0, y: 0, w: 100, h: 50 }; // centre (50,25)
  it("exits the right edge toward a point to the right", () => {
    const p = perimeterPoint(r, 1000, 25);
    expect(p).toEqual({ x: 100, y: 25 });
  });
  it("exits the top edge toward a point straight above", () => {
    const p = perimeterPoint(r, 50, -1000);
    expect(p).toEqual({ x: 50, y: 0 });
  });
  it("returns the centre when the target coincides with it", () => {
    expect(perimeterPoint(r, 50, 25)).toEqual({ x: 50, y: 25 });
  });
  it("clamps a diagonal to the nearer pair of edges", () => {
    // direction (1,1): tx = 50/1=50, ty = 25/1=25 -> t=25 -> hits top edge first
    const p = perimeterPoint(r, 150, 125);
    expect(p.y).toBe(50); // bottom edge (y grows down)
    expect(p.x).toBe(75);
  });
});

describe("rectContains", () => {
  const r = { x: 10, y: 20, w: 100, h: 50 };
  it("includes interior and edges", () => {
    expect(rectContains(r, 10, 20)).toBe(true);
    expect(rectContains(r, 110, 70)).toBe(true);
    expect(rectContains(r, 60, 45)).toBe(true);
  });
  it("excludes outside points", () => {
    expect(rectContains(r, 9, 45)).toBe(false);
    expect(rectContains(r, 60, 71)).toBe(false);
  });
});
