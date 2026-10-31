import { describe, it, expect } from "vitest";
import type { Rect } from "../../src/zoom/camera";
import { fitRoot, frameRect } from "../../src/zoom/camera";
import { buildScene } from "../../src/zoom/scene";
import { ENTER_FRACTION, FRAME_FILL, focusChain, focusId } from "../../src/zoom/focus-path";
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
    page_id: "",
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
  return {
    rootId: "root",
    nodes,
    worldRects,
    relationsByParent: new Map(),
    drawChildren: new Map(), externalLanes: new Map(),
    laidOutCount: 4,
  };
}

const VP = { w: 1000, h: 1000 };

describe("focusChain", () => {
  it("stays at the root until a child fills enough of the viewport", () => {
    // centre inside a (0.5 x 1), which at scale 600 covers 0.6 of the
    // viewport along its binding (height) axis: under 0.62, not entered
    const chain = focusChain(scene(), { cx: 0.25, cy: 0.5, scale: 600 }, VP);
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
    expect(focusId(scene(), { cx: 0.25, cy: 0.5, scale: 600 }, VP)).toBe("root");
  });
});

describe("focusChain after a dive", () => {
  // Regression: the chain measured a child against the viewport width alone,
  // while a dive frames by the binding axis, so on any landscape canvas a
  // double-click never counted as entering and the breadcrumb stayed at root.
  it.each([
    { w: 1400, h: 520 },
    { w: 1000, h: 1000 },
    { w: 600, h: 900 },
  ])("enters the framed node on a $w x $h viewport", (vp) => {
    for (const id of ["a", "b", "a1"]) {
      const cam = frameRect(vp, scene().worldRects.get(id)!, FRAME_FILL);
      expect(focusId(scene(), cam, vp)).toBe(id);
    }
  });

  it("enters the framed node when a sheet covers the right of the canvas", () => {
    const vp = { w: 1280, h: 720, insetRight: 332 };
    for (const id of ["a", "b", "a1"]) {
      const cam = frameRect(vp, scene().worldRects.get(id)!, FRAME_FILL);
      expect(focusId(scene(), cam, vp)).toBe(id);
    }
  });

  it("frames a dive past the entry threshold", () => {
    expect(FRAME_FILL).toBeGreaterThan(ENTER_FRACTION);
  });
});

describe("focusChain at the overview", () => {
  // A real packed layout (landscape cards), not the hand-made tall halves above.
  function packed(count: number): ZoomScene {
    const kids = Array.from({ length: count }, (_, i) => `c${i}`);
    const nodes = [
      { ...node("root", kids), kind: "system" as const },
      ...kids.map((id, i) => ({ ...node(id, []), parent_id: "root", level: 1, sibling_rank: i + 1 })),
    ];
    return buildScene({
      root_id: "root",
      project_name: "p",
      total_files: 0,
      max_depth: 1,
      truncated: false,
      nodes,
      relations: [],
    });
  }

  it.each([3, 4, 6, 9])("stays at the root on the overview fit with %i layers", (count) => {
    for (const vp of [{ w: 1600, h: 800 }, { w: 1000, h: 1000 }]) {
      expect(focusChain(packed(count), fitRoot(vp), vp).map((n) => n.id)).toEqual(["root"]);
    }
  });
});
