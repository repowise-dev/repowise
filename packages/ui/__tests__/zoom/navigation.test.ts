import { describe, it, expect } from "vitest";
import type { Rect } from "../../src/zoom/camera";
import { siblingInDirection, upTarget } from "../../src/zoom/navigation";
import type { ZoomScene } from "../../src/zoom/scene";
import type { ZoomNode } from "../../src/zoom/types";

function node(id: string, parent: string | null, children: string[] = []): ZoomNode {
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

// Two rows under root:  a  b  c
//                        d     e   (d sits a little right of a)
function scene(): ZoomScene {
  const ids = ["a", "b", "c", "d", "e"];
  const nodes = new Map<string, ZoomNode>([["root", node("root", null, ids)]]);
  for (const id of ids) nodes.set(id, node(id, "root"));
  const worldRects = new Map<string, Rect>([
    ["root", { x: 0, y: 0, w: 1, h: 1 }],
    ["a", { x: 0.0, y: 0.1, w: 0.2, h: 0.1 }],
    ["b", { x: 0.4, y: 0.1, w: 0.2, h: 0.1 }],
    ["c", { x: 0.8, y: 0.1, w: 0.2, h: 0.1 }],
    ["d", { x: 0.1, y: 0.5, w: 0.2, h: 0.1 }],
    ["e", { x: 0.7, y: 0.5, w: 0.2, h: 0.1 }],
  ]);
  return { rootId: "root", nodes, worldRects, relationsByParent: new Map(), drawChildren: new Map(), externalLanes: new Map(), laidOutCount: 6 };
}

describe("siblingInDirection", () => {
  it("moves along a row before dropping to the next", () => {
    expect(siblingInDirection(scene(), "a", "right")).toBe("b");
    expect(siblingInDirection(scene(), "b", "right")).toBe("c");
    expect(siblingInDirection(scene(), "b", "left")).toBe("a");
  });

  it("moves between rows to the nearest card across", () => {
    expect(siblingInDirection(scene(), "a", "down")).toBe("d");
    expect(siblingInDirection(scene(), "c", "down")).toBe("e");
    expect(siblingInDirection(scene(), "e", "up")).toBe("c");
  });

  it("returns null at an edge and for the root", () => {
    expect(siblingInDirection(scene(), "c", "right")).toBeNull();
    expect(siblingInDirection(scene(), "a", "up")).toBeNull();
    expect(siblingInDirection(scene(), "root", "left")).toBeNull();
  });
});

describe("upTarget", () => {
  const VP = { w: 1000, h: 1000 };
  // root > p > q, where q fills most of p (so framing p keeps q in focus)
  // root > p > r, a small card beside it
  function deep(): ZoomScene {
    const nodes = new Map<string, ZoomNode>([
      ["root", node("root", null, ["p"])],
      ["p", node("p", "root", ["q"])],
      ["q", node("q", "p", ["s"])],
      ["s", node("s", "q")],
    ]);
    const worldRects = new Map<string, Rect>([
      ["root", { x: 0, y: 0, w: 1, h: 1 }],
      ["p", { x: 0.1, y: 0.1, w: 0.4, h: 0.4 }],
      ["q", { x: 0.12, y: 0.12, w: 0.36, h: 0.36 }],
      ["s", { x: 0.2, y: 0.2, w: 0.1, h: 0.1 }],
    ]);
    return { rootId: "root", nodes, worldRects, relationsByParent: new Map(), drawChildren: new Map(), externalLanes: new Map(), laidOutCount: 4 };
  }
  const chain = (sc: ZoomScene, ids: string[]) => ids.map((id) => sc.nodes.get(id)!);

  it("goes to the parent when framing it leaves the current focus", () => {
    const sc = scene();
    sc.nodes.get("a")!.children = ["a1"];
    sc.nodes.set("a1", node("a1", "a"));
    sc.worldRects.set("a1", { x: 0.02, y: 0.12, w: 0.06, h: 0.06 });
    expect(upTarget(sc, chain(sc, ["root", "a", "a1"]), VP)).toBe("a");
  });

  it("skips a parent its focused child fills, so up never looks stuck", () => {
    const sc = deep();
    expect(upTarget(sc, chain(sc, ["root", "p", "q", "s"]), VP)).toBe("q");
    // from q, framing p still shows q entered: go to the overview instead
    expect(upTarget(sc, chain(sc, ["root", "p", "q"]), VP)).toBeNull();
  });

  it("is the overview one level down or at the root", () => {
    const sc = scene();
    expect(upTarget(sc, chain(sc, ["root", "a"]), VP)).toBeNull();
    expect(upTarget(sc, chain(sc, ["root"]), VP)).toBeNull();
  });
});
