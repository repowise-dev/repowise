import { describe, it, expect } from "vitest";
import {
  MINIMAP_MIN_MARK_PX,
  minimapFrame,
  minimapToWorld,
  minimapViewRect,
  worldToMinimapRect,
} from "../../src/zoom/minimap";

const SIZE = 132;
const PAD = 6;
const F = minimapFrame({ x: 0, y: 0, w: 1, h: 1 }, SIZE, PAD);

describe("minimap mapping", () => {
  it("maps the unit square onto the padded box and back", () => {
    expect(worldToMinimapRect({ x: 0, y: 0, w: 1, h: 1 }, F)).toEqual({
      x: 6,
      y: 6,
      w: 120,
      h: 120,
    });
    const p = minimapToWorld(66, 36, F);
    expect(p.wx).toBeCloseTo(0.5);
    expect(p.wy).toBeCloseTo(0.25);
  });
});

describe("minimapFrame", () => {
  it("takes the shape of a wide root box, its longer side the size", () => {
    const f = minimapFrame({ x: 0, y: 0.25, w: 1, h: 0.5 }, SIZE, PAD);
    expect(f.w).toBe(SIZE);
    expect(f.h).toBe(72);
    expect(worldToMinimapRect({ x: 0, y: 0.25, w: 1, h: 0.5 }, f)).toEqual({ x: 6, y: 6, w: 120, h: 60 });
    expect(minimapToWorld(66, 36, f).wy).toBeCloseTo(0.5);
  });
});

describe("minimapViewRect", () => {
  it("draws the viewport's world extent", () => {
    // 1200x600 viewport at scale 2400 shows 0.5 x 0.25 world units around (0.5, 0.5)
    const r = minimapViewRect({ cx: 0.5, cy: 0.5, scale: 2400 }, { w: 1200, h: 600 }, F);
    expect(r.x).toBeCloseTo(6 + 0.25 * 120);
    expect(r.w).toBeCloseTo(60);
    expect(r.y).toBeCloseTo(6 + 0.375 * 120);
    expect(r.h).toBeCloseTo(30);
  });

  it("clips a view larger than the map to the minimap", () => {
    const r = minimapViewRect({ cx: 0.5, cy: 0.5, scale: 100 }, { w: 1200, h: 600 }, F);
    expect(r).toEqual({ x: 0, y: 0, w: SIZE, h: SIZE });
  });

  it("keeps a deep zoom visible as a minimum-size mark at the right spot", () => {
    const r = minimapViewRect({ cx: 0.25, cy: 0.75, scale: 1e6 }, { w: 1200, h: 600 }, F);
    expect(r.w).toBe(MINIMAP_MIN_MARK_PX);
    expect(r.h).toBe(MINIMAP_MIN_MARK_PX);
    expect(r.x + r.w / 2).toBeCloseTo(6 + 0.25 * 120, 1);
    expect(r.y + r.h / 2).toBeCloseTo(6 + 0.75 * 120, 1);
  });

  it("writes into the rect it is given", () => {
    const out = { x: 0, y: 0, w: 0, h: 0 };
    expect(minimapViewRect({ cx: 0.5, cy: 0.5, scale: 2400 }, { w: 1200, h: 600 }, F, out)).toBe(out);
  });
});
