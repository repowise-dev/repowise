import { describe, it, expect } from "vitest";
import {
  expandThresholds,
  fadeAlphas,
  leafCapScale,
  transitionT,
} from "../../src/zoom/zoom-transition";

describe("expandThresholds", () => {
  it("scales with canvas width within bounds", () => {
    const t = expandThresholds(1440);
    expect(t.start).toBeCloseTo(360); // 1440 * 0.25
    expect(t.end).toBeCloseTo(576); // 1440 * 0.40
    expect(t.end).toBeGreaterThan(t.start);
  });

  it("clamps tiny and huge canvases and stays non-degenerate", () => {
    const small = expandThresholds(100);
    expect(small.start).toBe(80); // START_MIN_PX
    expect(small.end).toBe(200); // END_MIN_PX
    const huge = expandThresholds(10000);
    expect(huge.start).toBe(450); // START_MAX_PX
    expect(huge.end).toBe(640); // END_MAX_PX
    expect(huge.end).toBeGreaterThan(huge.start);
  });
});

describe("transitionT", () => {
  const th = { start: 200, end: 400 };
  it("is 0 below start and 1 above end", () => {
    expect(transitionT(150, th, true)).toBe(0);
    expect(transitionT(500, th, true)).toBe(1);
  });
  it("interpolates linearly between", () => {
    expect(transitionT(300, th, true)).toBeCloseTo(0.5);
    expect(transitionT(250, th, true)).toBeCloseTo(0.25);
  });
  it("is always 0 for leaves regardless of width", () => {
    expect(transitionT(10000, th, false)).toBe(0);
  });
});

describe("fadeAlphas", () => {
  it("cross-fades body out and children in, conserving inherited alpha", () => {
    const a = fadeAlphas(1, 0.25);
    expect(a.body).toBeCloseTo(0.75);
    expect(a.child).toBeCloseTo(0.25);
    expect(a.body + a.child).toBeCloseTo(1);
  });
  it("scales by the inherited alpha", () => {
    const a = fadeAlphas(0.5, 0.5);
    expect(a.body).toBeCloseTo(0.25);
    expect(a.child).toBeCloseTo(0.25);
  });
});

describe("leafCapScale", () => {
  const th = { start: 200, end: 400 };
  it("is 1 for nodes with children (they reveal children instead)", () => {
    expect(leafCapScale(5000, th, true)).toBe(1);
  });
  it("is 1 for a leaf still under the end threshold", () => {
    expect(leafCapScale(300, th, false)).toBe(1);
  });
  it("shrinks an over-grown leaf back to the end size", () => {
    expect(leafCapScale(800, th, false)).toBeCloseTo(0.5); // 400 / 800
  });
});
