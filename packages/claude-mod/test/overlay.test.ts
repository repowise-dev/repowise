import { describe, expect, it } from "vitest";
import { GROUND, MAP_PALETTES, TERMINAL_DEFAULT, layoutMap, type MapLayout, type PlacedFile } from "../src/views/map";
import { NO_LIT, frameCells, framePixels, litPixels, notOnMap, resolveLit, rippleRadius, type Lit } from "../src/views/overlay";
import { decode, feed, importers } from "./django";
import { syntheticTree } from "./synthetic";

const QUERY = "django/db/models/query.py";
const MANAGER = "django/db/models/manager.py";
const HITS = ["tests/queries/tests.py", "tests/queries/test_qs_combinators.py", "tests/queries/test_iterator.py"];
const pal = MAP_PALETTES.dark;
const layout = layoutMap(feed.files, { columns: 180, rows: 50, caseInsensitive: true });

/** The recorded Django turn: two searches' matches, two files opened, query.py edited, its importers. */
const turn: Lit = { hits: HITS, reads: [MANAGER, QUERY], named: [QUERY], importers, edits: [QUERY], edit: QUERY, current: QUERY };

const fileOf = (l: MapLayout, path: string): PlacedFile => l.files[l.index.get(path.toLowerCase()) as number] as PlacedFile;

/** The colors a file's lit pixels hold. */
function colorsOf(l: MapLayout, pixels: Uint32Array, path: string): Set<number> {
  const b = litPixels(l, fileOf(l, path));
  const out = new Set<number>();
  for (let y = b.y0; y < b.y1; y++) for (let x = b.x0; x < b.x1; x++) out.add(pixels[y * l.width + x] as number);
  return out;
}

describe("resolveLit", () => {
  it("finds every lit path on the map, case aside where paths are case-insensitive", () => {
    const o = resolveLit(layout, { ...turn, reads: [MANAGER.toUpperCase(), QUERY] });
    expect(o.reads).toHaveLength(2);
    expect(o.importers).toHaveLength(importers.length);
    expect(o.edit).toBe(layout.index.get(QUERY));
    expect(o.named).toEqual([layout.index.get(QUERY)]);
  });

  it("on a case-sensitive map only the exact path matches, except named paths, which arrive lowercased", () => {
    const posix = layoutMap([{ file_path: "src/App.ts", score: 6, nloc: 10 }], { columns: 20, rows: 4, caseInsensitive: false });
    expect(resolveLit(posix, { ...NO_LIT, reads: ["src/app.ts"] }).reads).toEqual([]);
    expect(resolveLit(posix, { ...NO_LIT, reads: ["src/App.ts"], named: ["src/app.ts"] })).toMatchObject({ reads: [0], named: [0] });
  });

  it("counts touched files that are not on the map", () => {
    expect(notOnMap(layout, turn)).toBe(0);
    expect(notOnMap(layout, { ...turn, reads: [...turn.reads, "notes/todo.md"] })).toBe(1);
  });
});

describe("lighting", () => {
  const { pixels } = framePixels(layout, resolveLit(layout, turn));

  it("each role fills its whole tile in its own color, the strongest role winning", () => {
    expect(colorsOf(layout, pixels, QUERY)).toEqual(new Set([pal.edit]));
    expect(colorsOf(layout, pixels, MANAGER)).toEqual(new Set([pal.read]));
    expect(colorsOf(layout, pixels, HITS[0]!)).toEqual(new Set([pal.hit]));
    expect(colorsOf(layout, pixels, "django/db/models/base.py")).toEqual(new Set([pal.importer]));
  });

  it("a search's matches glow on whole tiles only: no lit pixel outside a lit file", () => {
    const lit = framePixels(layout, resolveLit(layout, { ...NO_LIT, hits: HITS })).pixels;
    const tiles = new Set<number>();
    for (const h of HITS) {
      const b = litPixels(layout, fileOf(layout, h));
      for (let y = b.y0; y < b.y1; y++) for (let x = b.x0; x < b.x1; x++) tiles.add(y * layout.width + x);
    }
    lit.forEach((c, i) => expect(c === layout.base[i] || tiles.has(i)).toBe(true));
  });

  it("a fresh search glows brighter, then settles", () => {
    const o = resolveLit(layout, { ...NO_LIT, hits: HITS });
    const at = (t: number) => [...colorsOf(layout, framePixels(layout, o, { kind: "flash", t }).pixels, HITS[0]!)][0];
    expect(at(0)).not.toBe(pal.hit);
    expect(at(1)).toBe(pal.hit);
  });

  it("importers ripple out from the edit, all of them at rest", () => {
    const o = resolveLit(layout, turn);
    expect(rippleRadius(layout, o)).toBeGreaterThan(0);
    expect(rippleRadius(layout, resolveLit(layout, { ...turn, importers: [] }))).toBe(0);
    const lit = (t?: number) =>
      importers.filter((p) => colorsOf(layout, framePixels(layout, o, t === undefined ? undefined : { kind: "ripple", t }).pixels, p).has(pal.importer)).length;
    expect(lit(0.05)).toBeLessThan(lit());
  });

  it("a lit file too small for a pixel still lights one full cell where it sits", () => {
    const tree = syntheticTree(10_000);
    const dense = layoutMap(tree.files.slice(0, 4_000), { columns: 120, rows: 24, caseInsensitive: true });
    expect(dense.dense).toBe(true);
    const tiny = dense.files.find((f) => f.px1 <= f.px0 || f.py1 <= f.py0)!;
    const b = litPixels(dense, tiny);
    expect([b.x1 - b.x0, b.y1 - b.y0, b.y0 % 2]).toEqual([1, 2, 0]);
    const { pixels: px } = framePixels(dense, resolveLit(dense, { ...NO_LIT, edit: tiny.path, current: tiny.path }));
    expect([px[b.y0 * dense.width + b.x0], px[(b.y0 + 1) * dense.width + b.x0]]).toEqual([MAP_PALETTES.dark.edit, MAP_PALETTES.dark.edit]);
  });
});

describe("names", () => {
  const o = resolveLit(layout, turn);
  const words = decode(frameCells(layout, o));

  it("the edit's name in amber, opened files in the terminal's own colour, cut out of the tiles", () => {
    const edit = o.names.find((n) => n.text === "query.py")!;
    expect(edit.color).toBe(pal.edit);
    expect(o.names.find((n) => n.text === "manager.py")?.color).toBe(TERMINAL_DEFAULT);
    for (const n of o.names) {
      const cell = (n.row * layout.columns + n.col) * 3;
      expect(String.fromCharCode(words[cell]!)).toBe(n.text[0]);
      expect([words[cell + 1], words[cell + 2]]).toEqual([n.color, TERMINAL_DEFAULT]);
    }
  });

  it("names never overlap and stay on the map", () => {
    const busy = resolveLit(layout, { ...turn, reads: feed.files.slice(0, 60).map((f) => f.file_path) });
    const cells = busy.names.flatMap((n) => [...n.text].map((_, k) => n.row * 1000 + n.col + k));
    expect(new Set(cells).size).toBe(cells.length);
    expect(busy.names.every((n) => n.col >= 0 && n.col + n.text.length <= layout.columns && n.row < layout.rows)).toBe(true);
  });
});

describe("frames", () => {
  it("an empty turn draws the base, and the ground stays the terminal's own", () => {
    const words = decode(frameCells(layout, resolveLit(layout, NO_LIT)));
    expect(framePixels(layout, resolveLit(layout, NO_LIT)).pixels).toEqual(layout.base);
    expect(layout.base.includes(GROUND)).toBe(true);
    for (let i = 0; i < words.length; i += 3) expect(words[i + 2] === GROUND).toBe(false);
  });

  it("a lit frame at 180x50 stays well under budget (5x the bench's 16 ms)", () => {
    const o = resolveLit(layout, turn);
    frameCells(layout, o, { kind: "ripple", t: 0.5 });
    const start = performance.now();
    frameCells(layout, resolveLit(layout, turn), { kind: "ripple", t: 0.5 });
    expect(performance.now() - start).toBeLessThan(80);
  });
});
