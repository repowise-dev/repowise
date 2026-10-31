import { describe, expect, it } from "vitest";

import { cardTextCache } from "../../src/zoom/card-measure";
import { drawCardText } from "../../src/zoom/card-text";
import type { ZoomPalette } from "../../src/zoom/theme";
import type { ZoomNode } from "../../src/zoom/types";

/**
 * A 2D-context stand-in: every method is a no-op except `measureText` (a
 * counted monospace measure at 0.6em of the current font) and `fillText`
 * (records what was drawn).
 */
function stubCtx() {
  const state = { measures: 0, texts: [] as string[], px: 10 };
  const ctx = new Proxy(
    {},
    {
      get(_t, prop) {
        if (prop === "measureText") {
          return (s: string) => {
            state.measures++;
            return { width: s.length * 0.6 * state.px };
          };
        }
        if (prop === "fillText") return (s: string) => state.texts.push(s);
        if (prop === "font") return `${state.px}px`;
        return () => undefined;
      },
      set(_t, prop, value) {
        if (prop === "font") state.px = Number(/(\d+)px/.exec(String(value))?.[1] ?? 10);
        return true;
      },
    },
  ) as unknown as CanvasRenderingContext2D;
  return { ctx, state };
}

const palette = new Proxy({}, { get: () => "black" }) as ZoomPalette;

function node(i: number, over: Partial<ZoomNode> = {}): ZoomNode {
  return {
    id: `n${i}`,
    parent_id: null,
    level: 1,
    kind: "group",
    name: `Command Line Interface ${i}`,
    path: "",
    children: [],
    importance: 0.5,
    sibling_rank: i,
    metrics: {
      file_count: 40 + i,
      descendant_count: 40,
      hotspot_count: i % 3,
      dead_count: 0,
      entry_point_count: 1,
      on_flow_count: 0,
    },
    summary: `Turns command-line requests and hook payloads into repository operations ${i}, rendered output, or hook responses for agents and people.`,
    language: null,
    page_id: "",
    health_score: 7,
    is_entry_point: false,
    is_hotspot: false,
    is_dead: false,
    is_test: false,
    on_flow: false,
    ...over,
  };
}

describe("drawCardText", () => {
  it("draws name, summary and footer on a root-sized card", () => {
    const { ctx, state } = stubCtx();
    drawCardText(ctx, { x: 0, y: 0, w: 150, h: 90 }, node(1), palette, 1, false);
    expect(state.texts.join("|")).toContain("Command Line");
    expect(state.texts.some((t) => t.startsWith("Turns"))).toBe(true);
    expect(state.texts).toContain("GROUP");
  });

  it("draws only the name on a tiny card", () => {
    const { ctx, state } = stubCtx();
    drawCardText(ctx, { x: 0, y: 0, w: 70, h: 40 }, node(1, { name: "Core" }), palette, 1, false);
    expect(state.texts).toEqual(["Core"]);
  });

  it("keeps the footer, and the language, when a two-line name leaves no room for chips", () => {
    const { ctx, state } = stubCtx();
    const file = node(1, { kind: "file", name: "very_long_module_name.py", language: "python" });
    drawCardText(ctx, { x: 0, y: 0, w: 120, h: 72 }, file, palette, 1, false);
    expect(state.texts).toContain("FILE");
    expect(state.texts).not.toContain("python");
    expect(state.texts).toContain("Python");
  });

  it("hits the text cache on a repeat frame (no measuring per frame)", () => {
    cardTextCache.clear();
    const { ctx, state } = stubCtx();
    const nodes = Array.from({ length: 120 }, (_, i) => node(i));
    const rects = nodes.map((_, i) => ({ x: i, y: 0, w: 130 + (i % 7) * 10, h: 87 + (i % 5) * 8 }));
    const frame = (): void => {
      for (let i = 0; i < nodes.length; i++) {
        drawCardText(ctx, rects[i]!, nodes[i]!, palette, 1, i % 2 === 0);
      }
    };
    frame();
    const cold = state.measures;
    frame();
    expect(cold).toBeGreaterThan(0);
    expect(state.measures - cold).toBe(0);
  });
});
