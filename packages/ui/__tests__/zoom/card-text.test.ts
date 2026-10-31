import { describe, expect, it } from "vitest";

import { cardTextCache } from "../../src/zoom/card-measure";
import { drawCardText } from "../../src/zoom/card-text";
import type { ZoomPalette } from "../../src/zoom/theme";
import type { ZoomNode } from "../../src/zoom/types";

/**
 * A 2D-context stand-in: every method is a no-op except `measureText` (a
 * counted monospace measure at 0.6em of the current font), `fillText`
 * (records what was drawn) and `roundRect` (counted: the composition bar).
 */
function stubCtx() {
  const state = { measures: 0, texts: [] as string[], px: 10, roundRects: 0 };
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
        if (prop === "roundRect") return () => state.roundRects++;
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
  it("draws name, corner score, summary and footer on a root-sized card", () => {
    const { ctx, state } = stubCtx();
    drawCardText(ctx, { x: 0, y: 0, w: 170, h: 90 }, node(1), palette, 1);
    expect(state.texts.join("|")).toContain("Command Line");
    expect(state.texts).toContain("7.0");
    expect(state.texts.some((t) => t.startsWith("Turns"))).toBe(true);
    expect(state.texts).toContain("41 files");
    // No kind label: the glyph says it.
    expect(state.texts).not.toContain("GROUP");
  });

  it("counts only the non-zero roles in the footer", () => {
    const { ctx, state } = stubCtx();
    // node(3): hotspot_count 0, entry_point_count 1, dead_count 0.
    drawCardText(ctx, { x: 0, y: 0, w: 300, h: 200 }, node(3), palette, 1);
    expect(state.texts).toContain("1");
    expect(state.texts).not.toContain("0");
  });

  it("draws only the name and score on a tiny card, and no score when unscored", () => {
    const { ctx, state } = stubCtx();
    drawCardText(ctx, { x: 0, y: 0, w: 80, h: 40 }, node(1, { name: "Core" }), palette, 1);
    expect(state.texts).toEqual(["7.0", "Core"]);
    const quiet = stubCtx();
    const unscored = node(1, { name: "Core", health_score: null });
    drawCardText(quiet.ctx, { x: 0, y: 0, w: 80, h: 40 }, unscored, palette, 1);
    expect(quiet.state.texts).toEqual(["Core"]);
  });

  it("drops the score on a tiny card rather than cut the name", () => {
    const { ctx, state } = stubCtx();
    drawCardText(ctx, { x: 0, y: 0, w: 70, h: 40 }, node(1, { name: "Core" }), palette, 1);
    expect(state.texts).toEqual(["Core"]);
  });

  it("joins technologies into one muted line", () => {
    const { ctx, state } = stubCtx();
    const n = node(1, { summary: "", technologies: ["python", "react"] });
    drawCardText(ctx, { x: 0, y: 0, w: 300, h: 200 }, n, palette, 1);
    expect(state.texts).toContain("Python · React");
  });

  it("gives a file no footer and no language label", () => {
    const { ctx, state } = stubCtx();
    const file = node(1, { kind: "file", name: "very_long_module_name.py", language: "python" });
    drawCardText(ctx, { x: 0, y: 0, w: 120, h: 72 }, file, palette, 1);
    expect(state.texts).not.toContain("FILE");
    expect(state.texts).not.toContain("Python");
    expect(state.texts).not.toContain("41 files");
  });

  it("draws the composition bar from the small tier up", () => {
    const { ctx, state } = stubCtx();
    const preview = { items: [], total: 0, bands: [1, 0, 2, 5, 0], files: 8 };
    const n = node(1, { name: "Core", summary: "" });
    drawCardText(ctx, { x: 0, y: 0, w: 110, h: 60 }, n, palette, 1, preview);
    expect(state.roundRects).toBe(1);
  });

  it("hits the text cache on a repeat frame (no measuring per frame)", () => {
    cardTextCache.clear();
    const { ctx, state } = stubCtx();
    const nodes = Array.from({ length: 120 }, (_, i) =>
      node(i, { health_score: i % 4 === 0 ? null : 3 + (i % 7) }),
    );
    const rects = nodes.map((_, i) => ({ x: i, y: 0, w: 130 + (i % 7) * 10, h: 87 + (i % 5) * 8 }));
    const frame = (): void => {
      for (let i = 0; i < nodes.length; i++) drawCardText(ctx, rects[i]!, nodes[i]!, palette, 1);
    };
    frame();
    const cold = state.measures;
    frame();
    expect(cold).toBeGreaterThan(0);
    expect(state.measures - cold).toBe(0);
  });
});
