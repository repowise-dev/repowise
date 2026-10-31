import { describe, expect, it } from "vitest";

import type { Camera, Rect } from "../../src/zoom/camera";
import { EDGE_MAX_PER_PARENT } from "../../src/zoom/constants";
import { drawScene, type DrawOptions } from "../../src/zoom/draw-tree";
import {
  BAND_FRAC,
  bucketExternalRelations,
  buildExternalLanes,
  EXTERNALS_MAX_DRAWN,
  focusLanes,
  LANE_UNITS,
  laneWithout,
} from "../../src/zoom/externals";
import { buildScene } from "../../src/zoom/scene";
import type { ZoomPalette } from "../../src/zoom/theme";
import type { ZoomExternal, ZoomNode } from "../../src/zoom/types";
import {
  countingContext,
  EXTERNAL_COUNT,
  HUB_FOLDER,
  syntheticMap,
  withExternals,
} from "./synthetic-map";

const map = withExternals(syntheticMap());
const scene = buildScene(map);
const palette = new Proxy({}, { get: () => "black" }) as ZoomPalette;
const VP = { w: 1600, h: 1000 };

/** A lane rect in world space. */
function toWorld(r: Rect, parent: Rect): Rect {
  const k = parent.w / LANE_UNITS;
  return { x: parent.x + r.x * k, y: parent.y + r.y * k, w: r.w * k, h: r.h * k };
}

function overlaps(a: Rect, b: Rect): boolean {
  return a.x < b.x + b.w && b.x < a.x + a.w && a.y < b.y + b.h && b.y < a.y + a.h;
}

function inside(inner: Rect, outer: Rect): boolean {
  const eps = 1e-12;
  return (
    inner.x >= outer.x - eps &&
    inner.y >= outer.y - eps &&
    inner.x + inner.w <= outer.x + outer.w + eps &&
    inner.y + inner.h <= outer.y + outer.h + eps
  );
}

function cameraOn(id: string, px: number): Camera {
  const r = scene.worldRects.get(id)!;
  return { cx: r.x + r.w / 2, cy: r.y + r.h / 2, scale: px / r.w };
}

function draw(cam: Camera, opts: Partial<DrawOptions> = {}) {
  const { ctx, counts } = countingContext();
  const stats = drawScene(ctx, scene, cam, VP, palette, {
    selectedId: null,
    hoveredId: null,
    lowDetail: false,
    ...opts,
  });
  return { stats, counts };
}

describe("bucketExternalRelations", () => {
  const node = (id: string, parent: string | null) => ({ id, parent_id: parent }) as ZoomNode;
  const nodes = new Map([
    ["p", node("p", null)],
    ["a", node("a", "p")],
    ["b", node("b", "x")],
  ]);
  const externals = new Map([["ext:db", { id: "ext:db" } as ZoomExternal]]);
  const rel = (source: string, target: string) => ({
    parent_id: "p",
    source_id: source,
    target_id: target,
    label: "imports",
    edge_count: 1,
    coupling: "loose",
  });

  it("keeps links from a real child to a known external only", () => {
    const out = bucketExternalRelations(
      [rel("a", "ext:db"), rel("b", "ext:db"), rel("a", "ext:gone"), rel("zz", "ext:db")],
      nodes,
      externals,
    );
    expect(out.get("p")!.map((r) => r.source_id)).toEqual(["a"]);
  });
});

describe("external lanes", () => {
  it("gives every parent with externals a lane", () => {
    expect(scene.externalLanes.has("root")).toBe(true);
    expect(scene.externalLanes.has(HUB_FOLDER)).toBe(true);
    expect(scene.externalLanes.size).toBeGreaterThan(600);
  });

  it("draws a bounded number of externals and folds the rest into one chip", () => {
    for (const lane of scene.externalLanes.values()) {
      expect(lane.cards.length).toBeLessThanOrEqual(EXTERNALS_MAX_DRAWN);
    }
    const root = scene.externalLanes.get("root")!;
    expect(root.cards).toHaveLength(EXTERNALS_MAX_DRAWN);
    expect(root.more?.label).toBe(`+${EXTERNAL_COUNT - EXTERNALS_MAX_DRAWN} more`);
    // Strongest first.
    for (let i = 1; i < root.cards.length; i++) {
      expect(root.cards[i - 1]!.edgeCount).toBeGreaterThanOrEqual(root.cards[i]!.edgeCount);
    }
  });

  it("never puts an external on a card: children pack above the band", () => {
    for (const [parentId, lane] of scene.externalLanes) {
      const parent = scene.worldRects.get(parentId)!;
      const bandY = parent.y + parent.h * (1 - BAND_FRAC);
      const kids = scene.nodes.get(parentId)!.children.map((id) => scene.worldRects.get(id)!);
      for (const k of kids) expect(k.y + k.h).toBeLessThanOrEqual(bandY + 1e-12);
      const boxes = lane.cards.map((c) => toWorld(c.rect, parent));
      if (lane.more) boxes.push(toWorld(lane.more.rect, parent));
      for (const b of boxes) {
        expect(inside(b, parent)).toBe(true);
        for (const k of kids) expect(overlaps(b, k)).toBe(false);
      }
      for (let i = 1; i < boxes.length; i++) expect(overlaps(boxes[i - 1]!, boxes[i]!)).toBe(false);
    }
  });

  it("routes each link from its child to its external card", () => {
    const lane = scene.externalLanes.get(HUB_FOLDER)!;
    for (const [source, links] of lane.bySource) {
      for (const l of links) {
        expect(l.sourceId).toBe(source);
        expect(lane.byTarget.get(l.card.id)).toContain(l);
        // The connector ends on the card's top edge, facing the children.
        expect(l.route.end.y).toBeCloseTo(l.card.rect.y, 2);
      }
    }
  });

  it("places each external under the child that imports it most, where there is room", () => {
    // A lane with room for every card: each sits centred under its top user.
    const parent = { x: 0, y: 0, w: 1, h: 0.5 };
    const worldRects = new Map<string, Rect>([
      ["p", parent],
      ["left", { x: 0.05, y: 0.05, w: 0.2, h: 0.1 }],
      ["right", { x: 0.7, y: 0.05, w: 0.2, h: 0.1 }],
    ]);
    const ext = (id: string) => ({ id, name: id, display_name: id, kind: "db", edge_count: 1 }) as ZoomExternal;
    const externals = new Map([["ext:a", ext("ext:a")], ["ext:b", ext("ext:b")]]);
    const rel = (source: string, target: string, edge_count: number) => ({
      parent_id: "p",
      source_id: source,
      target_id: target,
      label: "imports",
      edge_count,
      coupling: "loose",
    });
    const lane = buildExternalLanes(
      new Map([["p", [rel("left", "ext:a", 1), rel("right", "ext:a", 9), rel("left", "ext:b", 5)]]]),
      externals,
      worldRects,
    ).get("p")!;
    const centre = (id: string) => {
      const r = lane.cards.find((c) => c.id === id)!.rect;
      return r.x + r.w / 2;
    };
    expect(centre("ext:a")).toBeCloseTo(0.8 * LANE_UNITS, 6);
    expect(centre("ext:b")).toBeCloseTo(0.15 * LANE_UNITS, 6);
  });
});

describe("bands along the focus chain", () => {
  const root = scene.nodes.get(scene.rootId)!;
  const child = root.children.map((id) => scene.nodes.get(id)!).find((c) => scene.externalLanes.has(c.id))!;

  it("shows each external once: the outer band drops what the inner one draws", () => {
    const inner = scene.externalLanes.get(child.id)!;
    const outer = focusLanes(scene.externalLanes, [root, child]).get(root.id)!;
    expect(inner.cards.length).toBeGreaterThan(0);
    const innerIds = new Set(inner.cards.map((c) => c.id));
    expect(outer.cards.some((c) => innerIds.has(c.id))).toBe(false);
    expect(focusLanes(scene.externalLanes, [root, child]).has(child.id)).toBe(false);
  });

  it("drops from every outer band what any band inside it draws", () => {
    const grand = child.children.map((id) => scene.nodes.get(id)!).find((g) => scene.externalLanes.has(g.id));
    expect(grand).toBeDefined();
    if (!grand) return;
    const lanes = focusLanes(scene.externalLanes, [root, child, grand]);
    const seen = new Set<string>();
    for (const id of [grand.id, child.id, root.id]) {
      for (const c of (lanes.get(id) ?? scene.externalLanes.get(id)!).cards) {
        expect(seen.has(c.id)).toBe(false);
        seen.add(c.id);
      }
    }
  });

  it("trims a band to exactly the lane a fresh build over the remaining externals gives", () => {
    const lane = scene.externalLanes.get(root.id)!;
    const shown = new Set(lane.cards.slice(0, 2).map((c) => c.id));
    const { rels, externals, worldRects } = lane.inputs;
    const rest = rels.filter((r) => !shown.has(r.target_id));
    const fresh = buildExternalLanes(new Map([[root.id, rest]]), externals, worldRects).get(root.id);
    expect(laneWithout(lane, shown)).toEqual(fresh);
  });

  it("builds a trimmed band once per focus, not per call", () => {
    const a = focusLanes(scene.externalLanes, [root, child]).get(root.id);
    const b = focusLanes(scene.externalLanes, [root, child]).get(root.id);
    expect(a).toBe(b);
    expect(focusLanes(scene.externalLanes, [root]).size).toBe(0);
  });
});

describe("drawing external lanes", () => {
  it("draws the band with no connectors until something is focused", () => {
    const { counts } = draw(cameraOn("root", 900));
    expect(counts.get("setLineDash") ?? 0).toBeGreaterThan(0);
    expect(counts.get("bezierCurveTo") ?? 0).toBe(0);
  });

  it("hovering a child draws only its connectors, capped", () => {
    const layer = scene.nodes.get("root")!.children[0]!;
    const { counts } = draw(cameraOn("root", 900), { hoveredId: layer });
    const arrows = counts.get("bezierCurveTo") ?? 0;
    const own = scene.externalLanes.get("root")!.bySource.get(layer)!.length;
    expect(arrows).toBeGreaterThan(0);
    // Its relations to siblings plus its external connectors, each capped.
    expect(arrows).toBeLessThanOrEqual(EDGE_MAX_PER_PARENT + Math.min(own, EDGE_MAX_PER_PARENT));
  });

  it("hovering an external card draws who reaches it, capped", () => {
    const card = scene.externalLanes.get("root")!.cards[0]!;
    const { counts } = draw(cameraOn("root", 900), { hoveredId: card.id });
    const arrows = counts.get("bezierCurveTo") ?? 0;
    expect(arrows).toBeGreaterThan(0);
    expect(arrows).toBeLessThanOrEqual(EDGE_MAX_PER_PARENT);
  });

  it("adds hit-test entries for the drawn external cards", () => {
    const { stats } = draw(cameraOn("root", 900));
    const ext = stats.pick.filter((p) => p.id.startsWith("ext:"));
    expect(ext.length).toBeGreaterThan(0);
    expect(ext.length).toBeLessThanOrEqual(EXTERNALS_MAX_DRAWN * 12);
  });

  it("reuses the lanes across frames (built once per scene)", () => {
    const before = scene.externalLanes.get("root");
    draw(cameraOn("root", 900), { hoveredId: before!.cards[0]!.id });
    expect(scene.externalLanes.get("root")).toBe(before);
  });

  it("keeps a hovered frame with externals well inside a frame budget", () => {
    const cam = cameraOn(HUB_FOLDER, 1400);
    const hub = `${HUB_FOLDER}/0`;
    draw(cam, { hoveredId: hub });
    const runs = 20;
    const t0 = performance.now();
    for (let i = 0; i < runs; i++) draw(cam, { hoveredId: hub });
    expect((performance.now() - t0) / runs).toBeLessThan(16);
  });
});
