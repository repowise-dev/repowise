import { describe, expect, it, vi } from "vitest";

import { type Camera, cameraAcrossRelayout, fitRoot, worldRectToScreen } from "../../src/zoom/camera";
import { rootRect } from "../../src/zoom/geometry";
import {
  RELAYOUT_ASPECT_TOLERANCE,
  carryCamera,
  sceneRootRect,
  settleRootAspect,
} from "../../src/zoom/relayout";
import * as layout from "../../src/zoom/layout";
import { buildScene, testLayerIds } from "../../src/zoom/scene";
import { syntheticMap, TEST_LAYER, withExternals, withTestLayer } from "./synthetic-map";

const VP = { w: 1200, h: 680 };

describe("rootRect", () => {
  it("is the unit square by default", () => {
    expect(rootRect()).toEqual({ x: 0, y: 0, w: 1, h: 1 });
  });

  it("is the widest box of the aspect, centred in the unit square", () => {
    const wide = rootRect(2);
    expect(wide).toEqual({ x: 0, y: 0.25, w: 1, h: 0.5 });
    const tall = rootRect(0.5);
    expect(tall).toEqual({ x: 0.25, y: 0, w: 0.5, h: 1 });
  });

  it("clamps an extreme aspect", () => {
    expect(rootRect(50).w / rootRect(50).h).toBeCloseTo(3);
    expect(rootRect(0).w).toBe(1); // a degenerate aspect falls back to square
  });
});

describe("overview fit", () => {
  it("fills the viewport when the root matches its aspect", () => {
    const root = rootRect(VP.w / VP.h);
    const s = worldRectToScreen(fitRoot(VP, root), VP, root);
    expect(s.w / VP.w).toBeCloseTo(0.9);
    expect(s.h / VP.h).toBeCloseTo(0.9);
  });
});

describe("cameraAcrossRelayout", () => {
  it("keeps the node's point at the centre and its on-screen width", () => {
    const from = { x: 0.1, y: 0.2, w: 0.2, h: 0.1 };
    const to = { x: 0.5, y: 0.4, w: 0.1, h: 0.05 };
    const cam: Camera = { cx: 0.15, cy: 0.25, scale: 3000 };
    const next = cameraAcrossRelayout(cam, from, to);
    expect(next.cx).toBeCloseTo(0.525);
    expect(next.cy).toBeCloseTo(0.425);
    expect(worldRectToScreen(next, VP, to).w).toBeCloseTo(worldRectToScreen(cam, VP, from).w);
  });
});

describe("cameraAcrossRelayout with a reshaped root", () => {
  it("keeps the on-screen area when the shape changes", () => {
    const from = { x: 0, y: 0.25, w: 1, h: 0.5 };
    const to = { x: 0.25, y: 0, w: 0.5, h: 1 };
    const next = cameraAcrossRelayout({ cx: 0.5, cy: 0.5, scale: 1000 }, from, to);
    expect(next.scale).toBeCloseTo(1000);
  });
});

describe("carryCamera", () => {
  const map = syntheticMap();
  const square = buildScene(map);
  const wide = buildScene(map, { rootAspect: 1.8 });

  it("refits the overview", () => {
    const cam = carryCamera(square, wide, fitRoot(VP, sceneRootRect(square)), VP);
    expect(cam).toEqual(fitRoot(VP, sceneRootRect(wide)));
  });

  it("follows the focused node to its new place", () => {
    const layer = square.nodes.get(square.rootId)!.children[0]!;
    const from = square.worldRects.get(layer)!;
    const cam = { cx: from.x + from.w / 2, cy: from.y + from.h / 2, scale: (VP.w * 0.8) / from.w };
    const next = carryCamera(square, wide, cam, VP);
    const to = wide.worldRects.get(layer)!;
    expect(next.cx).toBeCloseTo(to.x + to.w / 2);
    expect(next.cy).toBeCloseTo(to.y + to.h / 2);
    expect(to.w * next.scale).toBeCloseTo(VP.w * 0.8);
  });
});

describe("settleRootAspect", () => {
  it("adopts the canvas aspect once it drifts past the tolerance", () => {
    expect(settleRootAspect(1, 1200, 680)).toBeCloseTo(1.76);
  });

  it("returns the current value itself for jitter", () => {
    const current = 1.76;
    const w = 680 * current * (1 + RELAYOUT_ASPECT_TOLERANCE / 2);
    expect(settleRootAspect(current, w, 680)).toBe(current);
  });

  it("ignores an unmeasured canvas", () => {
    expect(settleRootAspect(1.5, 0, 0)).toBe(1.5);
  });
});

describe("re-laying out the same map to a new root shape", () => {
  const map = withExternals(syntheticMap());
  const before = buildScene(map, { rootAspect: 1 });
  const packs = vi.spyOn(layout, "packLayout");
  const after = buildScene(map, { rootAspect: 1.6 });
  const packed = packs.mock.calls.length;
  packs.mockRestore();
  // A copy of the map shares nothing with the scenes above: a cold build.
  const cold = buildScene({ ...map }, { rootAspect: 1.6 });

  it("repacks only the root and keeps what does not depend on the shape", () => {
    expect(packed).toBe(1);
    expect(after.drawChildren).toBe(before.drawChildren);
    expect(after.relationsByParent).toBe(before.relationsByParent);
    expect(after.nodes).toBe(before.nodes);
  });

  it("lays out exactly what a cold build of that shape does", () => {
    expect(after.worldRects.size).toBe(cold.worldRects.size);
    for (const [id, r] of cold.worldRects) {
      const got = after.worldRects.get(id)!;
      for (const k of ["x", "y", "w", "h"] as const) expect(got[k]).toBeCloseTo(r[k], 12);
    }
    expect([...after.externalLanes.keys()]).toEqual([...cold.externalLanes.keys()]);
    for (const [id, lane] of cold.externalLanes) {
      const got = after.externalLanes.get(id)!;
      expect(got.cards.map((c) => c.id)).toEqual(lane.cards.map((c) => c.id));
      got.cards.forEach((c, i) => expect(c.rect.h).toBeCloseTo(lane.cards[i]!.rect.h, 9));
    }
  });
});

describe("a map with a test layer", () => {
  const map = withTestLayer(withExternals(syntheticMap()));
  const scene = buildScene(map, { rootAspect: 1.76 });
  const root = scene.nodes.get(scene.rootId)!;
  const layers = root.children.filter((id) => id !== TEST_LAYER);

  it("knows the test layer by what it holds", () => {
    expect([...testLayerIds(map)]).toEqual([TEST_LAYER]);
    expect(testLayerIds(syntheticMap()).size).toBe(0);
  });

  it("sets the test layer aside, right of every other layer", () => {
    const test = scene.worldRects.get(TEST_LAYER)!;
    for (const id of layers) {
      const r = scene.worldRects.get(id)!;
      expect(r.x + r.w).toBeLessThan(test.x);
    }
  });

  it("still repacks only the root on a new root shape", () => {
    const packs = vi.spyOn(layout, "packLayout");
    const wide = buildScene(map, { rootAspect: 2.2 });
    expect(packs.mock.calls.length).toBe(1);
    packs.mockRestore();
    expect(wide.worldRects.get(TEST_LAYER)!.x).toBeGreaterThan(wide.worldRects.get(layers[0]!)!.x);
  });
});
