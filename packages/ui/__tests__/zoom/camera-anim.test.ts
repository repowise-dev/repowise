import { describe, it, expect } from "vitest";
import type { Camera } from "../../src/zoom/camera";
import {
  easeInOutCubic,
  flyDuration,
  interpolateCamera,
} from "../../src/zoom/camera-anim";

describe("easeInOutCubic", () => {
  it("pins the endpoints and the midpoint", () => {
    expect(easeInOutCubic(0)).toBe(0);
    expect(easeInOutCubic(1)).toBe(1);
    expect(easeInOutCubic(0.5)).toBeCloseTo(0.5, 9);
  });
  it("clamps out-of-range progress", () => {
    expect(easeInOutCubic(-1)).toBe(0);
    expect(easeInOutCubic(2)).toBe(1);
  });
});

describe("interpolateCamera", () => {
  const from: Camera = { cx: 0, cy: 0, scale: 100 };
  const to: Camera = { cx: 1, cy: 2, scale: 10_000 };

  it("returns the endpoints at t=0 and t=1", () => {
    expect(interpolateCamera(from, to, 0)).toEqual(from);
    const end = interpolateCamera(from, to, 1);
    expect(end.cx).toBeCloseTo(1, 9);
    expect(end.cy).toBeCloseTo(2, 9);
    expect(end.scale).toBeCloseTo(10_000, 3);
  });

  it("interpolates scale geometrically, not linearly", () => {
    // eased(0.5) = 0.5 -> scale = 100 * (10000/100)^0.5 = 100 * 100 = 1000
    const mid = interpolateCamera(from, to, 0.5);
    expect(mid.scale).toBeCloseTo(1000, 3);
  });
});

describe("flyDuration", () => {
  const base: Camera = { cx: 0.5, cy: 0.5, scale: 1000 };
  it("stays within the clamp range", () => {
    const d = flyDuration(base, { cx: 0.5, cy: 0.5, scale: 1000 });
    expect(d).toBeGreaterThanOrEqual(260);
    expect(d).toBeLessThanOrEqual(720);
  });
  it("a tiny move is quicker than a cross-system jump", () => {
    const small = flyDuration(base, { cx: 0.5, cy: 0.5, scale: 1100 });
    const big = flyDuration(base, { cx: 0.5, cy: 0.5, scale: 4_000_000 });
    expect(small).toBeLessThan(big);
  });
});
