import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ZoomRenderer } from "../../src/zoom/renderer";
import { buildScene } from "../../src/zoom/scene";
import type { ZoomPalette } from "../../src/zoom/theme";
import { countingContext, syntheticMap } from "./synthetic-map";

/**
 * The render loop paints only when something changed, draws motion frames
 * without shadows, owes exactly one full repaint once the camera settles, and
 * then schedules nothing.
 */

const palette = new Proxy({}, { get: () => "black" }) as ZoomPalette;
const map = syntheticMap();
const scene = buildScene(map);

let frames: Array<() => void> = [];
let clock = 0;

beforeEach(() => {
  frames = [];
  clock = 1000;
  vi.stubGlobal("requestAnimationFrame", (cb: () => void) => frames.push(cb));
  vi.stubGlobal("cancelAnimationFrame", () => {});
  vi.spyOn(performance, "now").mockImplementation(() => clock);
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

function setup(onFocus?: () => void) {
  const { ctx, counts } = countingContext();
  const canvas = document.createElement("canvas");
  Object.defineProperty(canvas, "clientWidth", { value: 1200 });
  Object.defineProperty(canvas, "clientHeight", { value: 800 });
  canvas.getContext = (() => ctx) as unknown as HTMLCanvasElement["getContext"];
  let draws = 0;
  const hovers: Array<string | null> = [];
  const renderer = new ZoomRenderer({
    canvas,
    palette,
    onStats: () => draws++,
    onHover: (hit) => hovers.push(hit?.id ?? null),
    onFocus,
  });
  renderer.setScene(scene);
  /** Run the queued frame(s); report draws and whether shadows were painted. */
  const tick = () => {
    counts.clear();
    const before = draws;
    const run = frames;
    frames = [];
    for (const f of run) f();
    return { drew: draws - before, shadows: (counts.get("set:shadowBlur") ?? 0) > 0 };
  };
  return { renderer, tick, hovers };
}

describe("ZoomRenderer loop", () => {
  it("goes idle after the first paint", () => {
    const { tick } = setup();
    expect(tick()).toEqual({ drew: 1, shadows: true });
    expect(frames).toHaveLength(0);
  });

  it("does not repaint for a hover that does not change", () => {
    const { renderer, tick } = setup();
    tick();
    renderer.setHovered("l:0");
    expect(tick().drew).toBe(1);
    renderer.setHovered("l:0");
    expect(frames).toHaveLength(0);
  });

  it("picks once per frame for a burst of pointer moves and paints it that frame", () => {
    const { renderer, tick, hovers } = setup();
    tick();
    // jsdom's bounding box is at the origin, so client points are canvas points.
    for (let i = 0; i < 50; i++) renderer.pointAt(590 + i * 0.1, 400);
    expect(frames).toHaveLength(1);
    const pick = vi.spyOn(renderer, "pickEntry");
    expect(tick().drew).toBe(1);
    expect(pick).toHaveBeenCalledTimes(1);
    expect(hovers).toHaveLength(1);
    expect(hovers[0]).not.toBeNull();
  });

  it("draws a zoom without shadows, then one full repaint once settled", () => {
    const { renderer, tick } = setup();
    tick();
    const cam = renderer.getCamera();
    renderer.setCamera({ ...cam, scale: cam.scale * 1.1 });
    expect(tick()).toEqual({ drew: 1, shadows: false });
    clock += 50; // still settling: the loop ticks without painting
    expect(tick()).toEqual({ drew: 0, shadows: false });
    clock += 200;
    expect(tick()).toEqual({ drew: 1, shadows: true });
    expect(frames).toHaveLength(0);
  });

  it("draws a pan reduced, then settles at full quality", () => {
    const { renderer, tick } = setup();
    renderer.setHovered("l:0");
    tick();
    const cam = renderer.getCamera();
    renderer.setCamera({ ...cam, cx: cam.cx + 0.01 });
    renderer.markPanFrame();
    expect(tick()).toEqual({ drew: 1, shadows: false });
    clock += 200;
    expect(tick()).toEqual({ drew: 1, shadows: true });
    expect(frames).toHaveLength(0);
  });

  it("re-picks under a resting pointer once a zoom moves the cards", () => {
    const { renderer, tick, hovers } = setup();
    tick();
    renderer.pointAt(600, 400);
    tick();
    const before = hovers.length;
    const cam = renderer.getCamera();
    renderer.setCamera({ ...cam, scale: cam.scale * 1.5 });
    const pick = vi.spyOn(renderer, "pickEntry");
    tick(); // draws the zoom, then queues a pick against it
    // One loop: the reduced frame's tick and the re-pick share a single frame.
    expect(frames).toHaveLength(1);
    tick();
    expect(pick).toHaveBeenCalledTimes(1);
    expect(hovers.length).toBe(before + 1);
    clock += 500;
    tick();
    tick();
    expect(frames).toHaveLength(0);
  });

  it("reports the focus again for new data, not for a re-layout of the same map", () => {
    const onFocus = vi.fn();
    const { renderer, tick } = setup(onFocus);
    tick();
    expect(onFocus).toHaveBeenCalledTimes(1);
    renderer.setScene(buildScene(map, { rootAspect: 1.4 }));
    tick();
    tick();
    expect(onFocus).toHaveBeenCalledTimes(1);
    renderer.setScene(buildScene({ ...map }, { rootAspect: 1.4 }));
    tick();
    expect(onFocus).toHaveBeenCalledTimes(2);
  });

  it("drops a pending hover pick once the pointer leaves", () => {
    const { renderer, tick, hovers } = setup();
    tick();
    renderer.pointAt(600, 400);
    renderer.setHovered(null);
    tick();
    expect(hovers).toHaveLength(0);
  });

  it("does not reallocate the backing store when the size is unchanged", () => {
    const { renderer } = setup();
    const canvas = (renderer as unknown as { canvas: HTMLCanvasElement }).canvas;
    const set = vi.spyOn(canvas, "width", "set");
    renderer.resize();
    expect(set).not.toHaveBeenCalled();
  });

  it("resizes the backing store when the pixel ratio changes without a CSS resize", () => {
    const listeners: Array<() => void> = [];
    const queries: string[] = [];
    vi.stubGlobal("matchMedia", (q: string) => {
      queries.push(q);
      return {
        addEventListener: (_: string, fn: () => void) => listeners.push(fn),
        removeEventListener: () => {},
      };
    });
    vi.stubGlobal("devicePixelRatio", 1);
    const { renderer } = setup();
    const canvas = (renderer as unknown as { canvas: HTMLCanvasElement }).canvas;
    expect(canvas.width).toBe(1200);
    vi.stubGlobal("devicePixelRatio", 2);
    listeners.at(-1)!();
    expect(canvas.width).toBe(2400);
    expect(queries.at(-1)).toBe("(resolution: 2dppx)");
  });
});
