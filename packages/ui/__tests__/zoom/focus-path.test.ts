import { describe, it, expect } from "vitest";
import type { Rect } from "../../src/zoom/camera";
import { focusChain, focusId } from "../../src/zoom/focus-path";
import type { ZoomScene } from "../../src/zoom/scene";
import type { ZoomNode } from "../../src/zoom/types";

function node(id: string, children: string[]): ZoomNode {
  return {
    id,
    parent_id: null,
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
    layout: null,
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

function scene(): ZoomScene {
  const nodes = new Map<string, ZoomNode>([
    ["root", node("root", ["a", "b"])],
    ["a", node("a", ["a1"])],
    ["b", node("b", [])],
    ["a1", node("a1", [])],
  ]);
  const worldRects = new Map<string, Rect>([
    ["root", { x: 0, y: 0, w: 1, h: 1 }],
    ["a", { x: 0, y: 0, w: 0.5, h: 1 }],
    ["b", { x: 0.5, y: 0, w: 0.5, h: 1 }],
    ["a1", { x: 0, y: 0, w: 0.5, h: 0.5 }],
  ]);
  return { rootId: "root", nodes, worldRects, relationsByParent: new Map(), laidOutCount: 4 };
}

const VP = { w: 1000, h: 1000 };

describe("focusChain", () => {
  it("stays at the root until a child fills enough of the viewport", () => {
    // centre inside a, but a only spans 0.5*1000 = 500px < 0.62*1000 -> not entered
    const chain = focusChain(scene(), { cx: 0.25, cy: 0.5, scale: 1000 }, VP);
    expect(chain.map((n) => n.id)).toEqual(["root"]);
  });

  it("descends into the entered subtree", () => {
    // scale 2000 -> a spans 1000px (entered), a1 spans 1000px (entered)
    const chain = focusChain(scene(), { cx: 0.25, cy: 0.25, scale: 2000 }, VP);
    expect(chain.map((n) => n.id)).toEqual(["root", "a", "a1"]);
  });

  it("does not descend into a sibling the centre is not over", () => {
    const chain = focusChain(scene(), { cx: 0.75, cy: 0.5, scale: 2000 }, VP);
    expect(chain.map((n) => n.id)).toEqual(["root", "b"]);
  });
});

describe("focusId", () => {
  it("returns the deepest entered node id", () => {
    expect(focusId(scene(), { cx: 0.25, cy: 0.25, scale: 2000 }, VP)).toBe("a1");
    expect(focusId(scene(), { cx: 0.25, cy: 0.5, scale: 1000 }, VP)).toBe("root");
  });
});
