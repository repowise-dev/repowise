// The pane's one animator: frames go through the closures of the latest hook
// (never a `$` kept from the hook that started it), and a frame that throws
// stops the animation without leaving the timer.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { Animator, type AnimIO } from "../src/animator";

function io(name: string, seen: string[]): AnimIO {
  return {
    redraw: () => seen.push(`${name}:redraw`),
    debug: (m) => seen.push(`${name}:debug:${m}`),
    blit: async (key) => {
      seen.push(`${name}:blit:${key}`);
      return {};
    },
  };
}

beforeEach(() => vi.useFakeTimers());
afterEach(() => vi.useRealTimers());

describe("Animator", () => {
  it("blits through the closures of the latest hook", async () => {
    const seen: string[] = [];
    const a = new Animator();
    a.play(io("first", seen), "rays", 1_000, () => ({ key: "k", cells: "", columns: 1, rows: 1 }));
    a.use(io("later", seen));
    await vi.advanceTimersByTimeAsync(45);
    expect(seen).toEqual(["first:redraw", "later:blit:k"]);
    a.end();
  });

  it("a frame that throws stops the animation and is logged, not thrown from the timer", async () => {
    const seen: string[] = [];
    const a = new Animator();
    a.play(io("hook", seen), "rays", 1_000, () => {
      throw new Error("bad frame");
    }, false);
    await vi.advanceTimersByTimeAsync(100);
    expect(seen).toEqual(["hook:debug:animation failed: Error: bad frame"]);
    expect(a.progress(Date.now())).toBeUndefined();
  });
});
