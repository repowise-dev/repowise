import { describe, it, expect } from "vitest";
import {
  type Camera,
  type Viewport,
  clampScale,
  fitRoot,
  frameRect,
  panByScreen,
  screenToWorld,
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
    const cam = fitRoot(VP, 0.9);
    expect(cam.cx).toBe(0.5);
    expect(cam.cy).toBe(0.5);
    expect(cam.scale).toBeCloseTo(800 * 0.9); // bounded by the shorter axis
  });

  it("frameRect centres and scales to fill the smaller axis", () => {
    const rect = { x: 0.2, y: 0.4, w: 0.1, h: 0.05 };
    const cam = frameRect(VP, rect, 0.8);
    expect(cam.cx).toBeCloseTo(0.25);
    expect(cam.cy).toBeCloseTo(0.425);
    // larger rect dimension (0.1) maps to 80% of the 800px axis
    expect(cam.scale).toBeCloseTo((800 * 0.8) / 0.1);
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
