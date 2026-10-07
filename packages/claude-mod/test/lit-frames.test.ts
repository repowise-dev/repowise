// The keys (the inspector's ring, zoom) must not change how the turn lights
// the map. test/fixtures/lit-frames-map-commit.json holds a hash of every frame
// the map commit painted for the inputs in ./lit-frames; with nothing selected,
// today's paint path must give the same frames.
import { createHash } from "node:crypto";
import { describe, expect, it } from "vitest";
import { layoutMap } from "../src/views/map";
import { frameCells, resolveLit, type Lit } from "../src/views/overlay";
import { feed } from "./django";
import { fixture } from "./fake-host";
import { SIZES, STEPS, STYLES, TURNS, caseName } from "./lit-frames";

const golden = JSON.parse(fixture("lit-frames-map-commit.json")) as Record<string, string>;
const hash = (cells: string): string => createHash("sha256").update(cells).digest("hex").slice(0, 16);

describe("lit colours and ripple frames, against the map commit", () => {
  it("covers files and folders: both kinds of map are in the golden", () => {
    const dense = SIZES.map(([columns, rows]) => layoutMap(feed.files, { columns, rows, caseInsensitive: true }).dense);
    expect(dense).toContain(true);
    expect(dense).toContain(false);
  });

  /** Every (size, style) layout, each once. */
  const layouts = SIZES.flatMap((size) => STYLES.map((style) => ({ size, style, layout: layoutMap(feed.files, { columns: size[0], rows: size[1], caseInsensitive: true, style }) })));

  /** The frames of one turn on one layout whose hash differs from the map commit's, by name. */
  function differing(turn: string, lit: Lit, at: (typeof layouts)[number]): string[] {
    const overlay = resolveLit(at.layout, lit);
    return STEPS.filter((step) => hash(frameCells(at.layout, overlay, step)) !== golden[caseName(turn, at.size, at.style, step)]).map((step) =>
      caseName(turn, at.size, at.style, step),
    );
  }

  for (const [turn, lit] of Object.entries(TURNS)) {
    it(`the ${turn} turn paints every frame as the map commit did`, () => {
      expect(layouts.flatMap((at) => differing(turn, lit, at))).toEqual([]);
    });
  }

});
