import { describe, it, expect } from "vitest";
import {
  type Camera,
  type Viewport,
  clampScale,
  fitRoot,
  cameraAfterResize,
  fitScale,
  frameRect,
  panByScreen,
  panToReveal,
  viewportCoverage,
  screenToWorld,
  wheelPixels,
  worldRectToScreen,
  worldToScreen,
  zoomAbout,
} from "../../src/zoom/camera";
import { MAX_SCALE, MIN_SCALE } from "../../src/zoom/constants";

const VP: Viewport = { w: 1000, h: 800 };

describe("camera transforms", () => {
  it("places the centre world point at the viewport centre", () => {
    const cam: Camera = { cx: 0.5, cy: 0.5, scale: 600 };
    const p = worldToScreen(cam, VP, 0.5, 0.5);
    expect(p.sx).toBeCloseTo(500);
    expect(p.sy).toBeCloseTo(400);
  });

  it("worldToScreen and screenToWorld are inverses", () => {
    const cam: Camera = { cx: 0.3, cy: 0.7, scale: 1234.5 };
    const w = screenToWorld(cam, VP, 137, 642);
    const s = worldToScreen(cam, VP, w.wx, w.wy);
    expect(s.sx).toBeCloseTo(137);
    expect(s.sy).toBeCloseTo(642);
  });

  it("maps a world rect's size by scale", () => {
    const cam: Camera = { cx: 0.5, cy: 0.5, scale: 1000 };
    const r = worldRectToScreen(cam, VP, { x: 0.25, y: 0.25, w: 0.5, h: 0.5 });
    expect(r.w).toBeCloseTo(500);
    expect(r.h).toBeCloseTo(500);
    // centred rect stays centred
    expect(r.x + r.w / 2).toBeCloseTo(500);
    expect(r.y + r.h / 2).toBeCloseTo(400);
  });
});

describe("zoomAbout", () => {
  it("keeps the world point under the anchor fixed", () => {
    const cam: Camera = { cx: 0.5, cy: 0.5, scale: 800 };
    const anchorSx = 720;
    const anchorSy = 210;
    const before = screenToWorld(cam, VP, anchorSx, anchorSy);
    const zoomed = zoomAbout(cam, VP, anchorSx, anchorSy, 2.5);
    const after = worldToScreen(zoomed, VP, before.wx, before.wy);
    expect(after.sx).toBeCloseTo(anchorSx, 4);
    expect(after.sy).toBeCloseTo(anchorSy, 4);
    expect(zoomed.scale).toBeCloseTo(2000);
  });

  it("respects scale clamps", () => {
    const cam: Camera = { cx: 0.5, cy: 0.5, scale: 100 };
    const out = zoomAbout(cam, VP, 500, 400, 1e-9);
    expect(out.scale).toBe(MIN_SCALE);
  });
});

describe("panByScreen", () => {
  it("shifts the centre by the inverse scaled delta", () => {
    const cam: Camera = { cx: 0.5, cy: 0.5, scale: 500 };
    const out = panByScreen(cam, 250, -100);
    expect(out.cx).toBeCloseTo(0.5 - 250 / 500);
    expect(out.cy).toBeCloseTo(0.5 + 100 / 500);
    expect(out.scale).toBe(500);
  });
});

describe("framing helpers", () => {
  it("fitRoot centres the unit square", () => {
    const cam = fitRoot(VP);
    expect(cam.cx).toBe(0.5);
    expect(cam.cy).toBe(0.5);
    expect(cam.scale).toBeCloseTo(800 * 0.9); // bounded by the shorter axis
  });

  it("frameRect centres and fits the rect's binding axis", () => {
    const rect = { x: 0.2, y: 0.4, w: 0.1, h: 0.05 };
    const cam = frameRect(VP, rect, 0.8);
    expect(cam.cx).toBeCloseTo(0.25);
    expect(cam.cy).toBeCloseTo(0.425);
    // 2:1 rect in a 1000x800 viewport: width binds, spanning 80% of 1000px
    expect(cam.scale).toBeCloseTo((1000 * 0.8) / 0.1);
    expect(viewportCoverage(cam, VP, rect)).toBeCloseTo(0.8);
  });

  it("frameRect fits a tall rect by its height", () => {
    const rect = { x: 0.2, y: 0.1, w: 0.05, h: 0.4 };
    const cam = frameRect(VP, rect, 0.8);
    expect(cam.scale).toBeCloseTo((800 * 0.8) / 0.4);
    expect(viewportCoverage(cam, VP, rect)).toBeCloseTo(0.8);
  });

  it("frameRect centres the rect in the clear area beside a covering sheet", () => {
    const vp: Viewport = { w: 1000, h: 800, insetRight: 300 };
    const rect = { x: 0.2, y: 0.4, w: 0.1, h: 0.05 };
    const cam = frameRect(vp, rect, 0.8);
    // width binds against the 700px clear width
    expect(cam.scale).toBeCloseTo((700 * 0.8) / 0.1);
    const s = worldRectToScreen(cam, vp, rect);
    expect(s.x + s.w / 2).toBeCloseTo(350);
    expect(s.x + s.w).toBeLessThan(700);
    expect(viewportCoverage(cam, vp, rect)).toBeCloseTo(0.8);
  });

  it("frameRect matches the old square fit for a square rect", () => {
    const rect = { x: 0, y: 0, w: 0.2, h: 0.2 };
    expect(frameRect(VP, rect, 0.8).scale).toBeCloseTo((800 * 0.8) / 0.2);
  });

  it("frameRect keeps the health map's disc framing: no inset, square rect, fill 0.9", () => {
    // use-map-camera frames a disc (world 0..1000) as its bounding square.
    // Pinned to the values frameRect returned before the inset-aware fit.
    const disc = { x: 300 - 40, y: 420 - 40, w: 80, h: 80 };
    expect(frameRect(VP, disc, 0.9)).toEqual({ cx: 300, cy: 420, scale: (800 * 0.9) / 80 });
    // and the default fill is unchanged
    expect(frameRect(VP, disc).scale).toBeCloseTo((800 * 0.8) / 80);
  });

  it("clampScale bounds both ends", () => {
    expect(clampScale(1e12)).toBe(MAX_SCALE);
    expect(clampScale(1e-12)).toBe(MIN_SCALE);
  });
});

describe("deep-zoom precision (centre-anchored rebase)", () => {
  it("keeps the centre O(1) and round-trips a world point at extreme scale", () => {
    const cam: Camera = { cx: 0.123456789, cy: 0.987654321, scale: 2_000_000 };
    expect(Math.abs(cam.cx)).toBeLessThan(2);
    expect(Math.abs(cam.cy)).toBeLessThan(2);
    const target = { wx: 0.1234567, wy: 0.9876543 };
    const s = worldToScreen(cam, VP, target.wx, target.wy);
    const back = screenToWorld(cam, VP, s.sx, s.sy);
    // sub-pixel world precision survives a 2e6 scale round-trip
    expect(back.wx).toBeCloseTo(target.wx, 9);
    expect(back.wy).toBeCloseTo(target.wy, 9);
  });
});

describe("cameraAfterResize", () => {
  it("keeps centre and scale when the user has moved, so nothing jumps", () => {
    const cam: Camera = { cx: 0.31, cy: 0.62, scale: 4000 };
    const next = cameraAfterResize(cam, VP, { w: 680, h: 800 });
    expect(next).toBe(cam);
    // the world point at the centre is still at the (new) centre
    const c = worldToScreen(next, { w: 680, h: 800 }, 0.31, 0.62);
    expect(c.sx).toBeCloseTo(340);
    expect(c.sy).toBeCloseTo(400);
  });

  it("refits a camera that was sitting on the overview fit", () => {
    const next = cameraAfterResize(fitRoot(VP), VP, { w: 600, h: 500 });
    expect(next).toEqual(fitRoot({ w: 600, h: 500 }));
  });

  it("fitScale is fitRoot's scale", () => {
    expect(fitScale(VP)).toBe(fitRoot(VP).scale);
  });
});

describe("panToReveal", () => {
  const cam: Camera = { cx: 0.5, cy: 0.5, scale: 1000 };
  const inset = 320;

  it("returns the same camera when the rect is already clear", () => {
    // screen x 100..300, clear area ends at 1000 - 320 - 16 = 664
    expect(panToReveal(cam, VP, { x: 0.1, y: 0.4, w: 0.2, h: 0.1 }, inset)).toBe(cam);
  });

  it("pans left just enough to clear the covered strip, never zooming", () => {
    const rect = { x: 0.7, y: 0.4, w: 0.2, h: 0.1 }; // screen 700..900
    const next = panToReveal(cam, VP, rect, inset);
    expect(next.scale).toBe(cam.scale);
    const r = worldRectToScreen(next, VP, rect);
    expect(r.x + r.w).toBeCloseTo(1000 - inset - 16);
  });

  it("pans on every edge, not only the sheet's", () => {
    const rect = { x: 0.4, y: 0.85, w: 0.1, h: 0.1 }; // screen y 750..850 on an 800px canvas
    const r = worldRectToScreen(panToReveal(cam, VP, rect, 0), VP, rect);
    expect(r.y + r.h).toBeCloseTo(800 - 16);
    expect(r.x).toBeCloseTo(400);
  });

  it("aligns the left edge of a rect wider than the clear area", () => {
    const rect = { x: 0.1, y: 0.4, w: 0.8, h: 0.1 }; // 800px wide, 664px clear
    const r = worldRectToScreen(panToReveal(cam, VP, rect, inset), VP, rect);
    expect(r.x).toBeCloseTo(16);
  });
});

describe("wheelPixels", () => {
  it("reads pixel, line and page wheels in px", () => {
    expect(wheelPixels({ deltaY: 100, deltaMode: 0 }, 800)).toBe(100);
    // A Firefox line-mode notch (3 lines) zooms like a ~48px pixel notch.
    expect(wheelPixels({ deltaY: 3, deltaMode: 1 }, 800)).toBe(48);
    expect(wheelPixels({ deltaY: -1, deltaMode: 2 }, 800)).toBe(-800);
  });
});
