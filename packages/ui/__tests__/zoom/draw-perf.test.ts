import { describe, it, expect } from "vitest";
import type { Camera } from "../../src/zoom/camera";
import { EDGE_MAX_PER_PARENT, MAX_CHILDREN_DRAWN } from "../../src/zoom/constants";
import { drawScene, type DrawOptions } from "../../src/zoom/draw-tree";
import { REST_EDGE_MAX } from "../../src/zoom/rest-links";
import { buildScene } from "../../src/zoom/scene";
import type { ZoomPalette } from "../../src/zoom/theme";
import {
  CAPPED_FOLDER,
  CAPPED_HUB,
  countingContext,
  HUB_FILE,
  HUB_FOLDER,
  syntheticMap,
} from "./synthetic-map";

/**
 * Work bounds for the draw pass on a repository-sized map. A hover repaints the
 * whole frame, so these are the hover budget too: the drawn set, the canvas
 * calls and the time must stay bounded by what is on screen, never by how many
 * nodes or relations the map holds.
 */

const palette = new Proxy({}, { get: () => "black" }) as ZoomPalette;
const VP = { w: 1600, h: 1000 };
const map = syntheticMap();
const scene = buildScene(map);

/** A camera centred on `id`, scaled so that box is `px` wide on screen. */
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

describe("draw pass on a repository-sized map", () => {
  it("is the size of a real index", () => {
    expect(map.nodes.length).toBeGreaterThan(7000);
    expect(map.relations.length).toBeGreaterThan(4000);
  });

  it("visits a bounded slice of the tree, not every node", () => {
    for (const cam of [cameraOn("root", 900), cameraOn(HUB_FOLDER, 1400), cameraOn(HUB_FILE, 120)]) {
      const { stats } = draw(cam);
      expect(stats.drawn + stats.culled).toBeLessThan(800);
    }
  });

  it("draws at most the per-parent cap of arrows for a hub with hundreds of relations", () => {
    const { counts } = draw(cameraOn(HUB_FILE, 120), { hoveredId: HUB_FILE });
    const arrows = counts.get("bezierCurveTo") ?? 0;
    expect(arrows).toBeGreaterThan(0);
    expect(arrows).toBeLessThanOrEqual(EDGE_MAX_PER_PARENT);
  });

  it("still draws arrows for a hub in a density-capped folder", () => {
    const { counts } = draw(cameraOn(CAPPED_HUB, 120), { hoveredId: CAPPED_HUB });
    const arrows = counts.get("bezierCurveTo") ?? 0;
    expect(arrows).toBeGreaterThan(0);
    expect(arrows).toBeLessThanOrEqual(EDGE_MAX_PER_PARENT);
  });

  it("adds only the arrows and their labels to a frame when a card is hovered", () => {
    const cam = cameraOn(HUB_FILE, 120);
    const base = draw(cam).counts;
    const hovered = draw(cam, { hoveredId: HUB_FILE }).counts;
    // One pill and one caption per labelled arrow, at most.
    const pills = (hovered.get("roundRect") ?? 0) - (base.get("roundRect") ?? 0);
    const captions = (hovered.get("fillText") ?? 0) - (base.get("fillText") ?? 0);
    expect(pills).toBeGreaterThan(0);
    expect(pills).toBeLessThanOrEqual(EDGE_MAX_PER_PARENT);
    expect(captions).toBe(pills);
  });

  it("draws at most the at-rest cap of arrows, and labels only those, before anything is hovered", () => {
    const cam = cameraOn(HUB_FOLDER, 1400);
    const base = draw(cam).counts;
    const rest = draw(cam, { levelId: HUB_FOLDER }).counts;
    const arrows = rest.get("bezierCurveTo") ?? 0;
    expect(arrows).toBeGreaterThan(0);
    expect(arrows).toBeLessThanOrEqual(REST_EDGE_MAX);
    const pills = (rest.get("roundRect") ?? 0) - (base.get("roundRect") ?? 0);
    expect(pills).toBeLessThanOrEqual(REST_EDGE_MAX);
  });

  it("bounds a hovered card's arrows plus the dimmed rest of its level", () => {
    const cam = cameraOn(HUB_FILE, 120);
    const { counts } = draw(cam, { levelId: HUB_FOLDER, hoveredId: HUB_FILE });
    expect(counts.get("bezierCurveTo") ?? 0).toBeLessThanOrEqual(EDGE_MAX_PER_PARENT + REST_EDGE_MAX);
  });

  it("draws no at-rest arrow mid-pan", () => {
    const { counts } = draw(cameraOn(HUB_FOLDER, 1400), { levelId: HUB_FOLDER, lowDetail: true });
    expect(counts.get("bezierCurveTo") ?? 0).toBe(0);
  });

  it("labels no arrow while the camera moves", () => {
    const cam = cameraOn(HUB_FILE, 120);
    const base = draw(cam, { motion: true }).counts;
    const hovered = draw(cam, { hoveredId: HUB_FILE, motion: true }).counts;
    expect(hovered.get("fillText")).toBe(base.get("fillText"));
  });

  it("repaints a hovered hub frame well inside a frame budget", () => {
    const cam = cameraOn(HUB_FILE, 120);
    draw(cam, { hoveredId: HUB_FILE, levelId: HUB_FOLDER }); // warm up
    const runs = 20;
    const t0 = performance.now();
    for (let i = 0; i < runs; i++) draw(cam, { hoveredId: HUB_FILE, levelId: HUB_FOLDER });
    // Measured ~1 ms; the ceiling only catches a regression to per-hover work
    // that scales with the map (a scan or sort of every relation or node).
    expect((performance.now() - t0) / runs).toBeLessThan(16);
  });

  it("drops card shadows while the camera moves and keeps the rest", () => {
    const cam = cameraOn(HUB_FOLDER, 1400);
    const still = draw(cam).counts;
    const moving = draw(cam, { motion: true }).counts;
    expect(still.get("set:shadowBlur") ?? 0).toBeGreaterThan(0);
    expect(moving.get("set:shadowBlur") ?? 0).toBe(0);
    expect(moving.get("fillText")).toBe(still.get("fillText"));
  });
});

describe("buildScene precomputation", () => {
  it("keeps each parent's relations strongest first", () => {
    for (const bucket of scene.relationsByParent.values()) {
      for (let i = 1; i < bucket.length; i++) {
        expect(bucket[i - 1]!.edge_count).toBeGreaterThanOrEqual(bucket[i]!.edge_count);
      }
    }
  });

  it("resolves each node's drawable children once", () => {
    expect(scene.drawChildren.get(HUB_FOLDER)!.length).toBe(271);
    expect(scene.drawChildren.has(HUB_FILE)).toBe(false);
    const capped = scene.drawChildren.get(CAPPED_FOLDER)!;
    expect(capped).toHaveLength(MAX_CHILDREN_DRAWN);
    // The least important tail (by sibling_rank) is the part dropped.
    expect(capped.map((n) => n.sibling_rank).sort((a, b) => b - a)[0]).toBe(MAX_CHILDREN_DRAWN);
  });
});
