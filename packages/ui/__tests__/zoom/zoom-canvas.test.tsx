import { Profiler } from "react";
import { act, render } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ZoomCanvas } from "../../src/zoom/ZoomCanvas";
import { buildScene } from "../../src/zoom/scene";
import { countingContext, syntheticMap } from "./synthetic-map";

/**
 * The React host around the renderer: how often it builds the scene and how
 * often a hover re-renders it.
 */

vi.mock("../../src/zoom/scene", async (importOriginal) => {
  const real = await importOriginal<typeof import("../../src/zoom/scene")>();
  return { ...real, buildScene: vi.fn(real.buildScene) };
});

let frames: Array<() => void> = [];
const runFrames = () =>
  act(() => {
    for (let i = 0; i < 5 && frames.length > 0; i++) {
      const run = frames;
      frames = [];
      for (const f of run) f();
    }
  });

beforeEach(() => {
  frames = [];
  vi.stubGlobal("requestAnimationFrame", (cb: () => void) => frames.push(cb));
  vi.stubGlobal("cancelAnimationFrame", () => {});
  vi.stubGlobal(
    "ResizeObserver",
    class {
      observe() {}
      disconnect() {}
    },
  );
  vi.stubGlobal("matchMedia", (q: string) => ({
    matches: false,
    media: q,
    addEventListener() {},
    removeEventListener() {},
  }));
  vi.spyOn(HTMLCanvasElement.prototype, "clientWidth", "get").mockReturnValue(1200);
  vi.spyOn(HTMLCanvasElement.prototype, "clientHeight", "get").mockReturnValue(750);
  const { ctx } = countingContext();
  vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue(ctx as never);
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

const map = syntheticMap();

describe("ZoomCanvas", () => {
  it("builds the scene once at mount, already shaped to the canvas", () => {
    const build = vi.mocked(buildScene);
    build.mockClear();
    render(<ZoomCanvas data={map} />);
    expect(build).toHaveBeenCalledTimes(1);
    expect(build.mock.calls[0]![1]).toEqual({ rootAspect: 1.6 });
  });

  it("re-renders for a new hovered card only, and moves the card for the same one", () => {
    let commits = 0;
    const { container } = render(
      <Profiler id="zoom" onRender={() => commits++}>
        <ZoomCanvas data={map} />
      </Profiler>,
    );
    runFrames();
    const canvas = container.querySelector("canvas")!;
    const move = (x: number, y: number) => {
      canvas.dispatchEvent(new MouseEvent("pointermove", { clientX: x, clientY: y, bubbles: true }));
      runFrames();
    };
    move(600, 375);
    const card = container.querySelector<HTMLElement>(".pointer-events-none.absolute.z-10");
    expect(card).not.toBeNull();
    const after = commits;
    move(604, 378);
    expect(commits).toBe(after);
    expect(card!.style.left).toBe("604px");
    expect(canvas.style.cursor).toBe("pointer");
  });
});
