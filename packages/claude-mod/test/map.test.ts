import { describe, expect, it } from "vitest";
import { DARK, DARK_CANVAS } from "@repowise-dev/ui/brand";
import { scopeParts } from "../src/views/copy";
import { GROUND, MAP_PALETTES, colorForScore, encodeCells, groupKeys, labelText, layoutMap, mix, type MapFile, type MapLayout, type PlacedFile, TERMINAL_DEFAULT, halfCell } from "../src/views/map";
import { decode, feed } from "./django";
import { syntheticTree } from "./synthetic";

/** Every pixel index a placed file owns. */
function pixelsOf(f: PlacedFile, width: number): number[] {
  const out: number[] = [];
  for (let y = f.py0; y < f.py1; y++) for (let x = f.px0; x < f.px1; x++) out.push(y * width + x);
  return out;
}

/** Which drawn file owns each pixel; throws if two claim one. */
function owners(layout: MapLayout): Int32Array {
  const grid = new Int32Array(layout.width * layout.height).fill(-1);
  for (const i of layout.drawn) {
    for (const p of pixelsOf(layout.files[i]!, layout.width)) {
      if (grid[p] !== -1) throw new Error(`pixel ${p} claimed twice`);
      grid[p] = i;
    }
  }
  return grid;
}

const distinctOwners = (layout: MapLayout) => new Set([...owners(layout)].filter((i) => i >= 0)).size;

describe("groupKeys", () => {
  it("splits a folder that would hold most of the map, judged on Django", () => {
    const keyed = groupKeys(feed.files);
    const keys = new Set(keyed);
    for (const k of ["django/db", "django/contrib", "tests/migrations", "scripts"]) expect(keys.has(k)).toBe(true);
    // What stays in `tests` is only the files directly in it.
    const direct = feed.files.filter((f) => f.file_path.split("/").length === 2 && f.file_path.startsWith("tests/"));
    expect(keyed.filter((k) => k === "tests")).toHaveLength(direct.length);
  });

  it("keeps root files and files directly in a split folder in their own group", () => {
    const files: MapFile[] = [
      { file_path: "README", score: null, nloc: 1 },
      { file_path: "a/x.py", score: 9, nloc: 50 },
      { file_path: "a/b/y.py", score: 9, nloc: 50 },
      { file_path: "a/c/z.py", score: 9, nloc: 50 },
    ];
    expect(groupKeys(files)).toEqual(["", "a", "a/b", "a/c"]);
  });

  it("leaves a heavy folder whole when splitting would not divide it", () => {
    const files: MapFile[] = [
      { file_path: "a/b/x.py", score: 9, nloc: 90 },
      { file_path: "c/y.py", score: 9, nloc: 10 },
    ];
    expect(groupKeys(files)).toEqual(["a", "c"]);
  });
});

describe("layoutMap on the Django feed at 180x50", () => {
  const layout = layoutMap(feed.files, { columns: 180, rows: 50, caseInsensitive: true });
  const total = feed.files.reduce((s, f) => s + f.nloc, 0);
  const canvas = 180 * 100;

  it("places every file with lines, with areas proportional to lines and inside the canvas", () => {
    expect(layout.files).toHaveLength(feed.files.filter((f) => f.nloc > 0).length);
    for (const f of layout.files) {
      expect(f.rect.w * f.rect.h).toBeCloseTo((f.nloc / total) * canvas, 6);
      expect(f.rect.x + f.rect.w).toBeLessThanOrEqual(180 + 1e-6);
      expect(f.rect.y + f.rect.h).toBeLessThanOrEqual(100 + 1e-6);
    }
    for (const g of layout.groups) expect(g.rect.w * g.rect.h).toBeCloseTo((g.nloc / total) * canvas, 6);
  });

  it("no two files own one pixel, and only drawn files own any", () => {
    expect(distinctOwners(layout)).toBe(layout.drawn.length);
    expect(layout.drawn.length).toBeGreaterThan(1_000);
    expect(layout.drawn.length).toBeLessThanOrEqual(feed.shown);
  });

  it("is deterministic, whatever order the feed arrives in", () => {
    const again = layoutMap([...feed.files].reverse(), { columns: 180, rows: 50, caseInsensitive: true });
    expect(again.files).toEqual(layout.files);
    expect(again.base).toEqual(layout.base);
    expect(again.labels).toEqual(layout.labels);
  });

  it("paints the ground and gutters, never the terminal's own background", () => {
    expect(GROUND).toBe(parseInt(DARK.bgRoot.slice(1), 16));
    expect([...layout.base].some((c) => c === GROUND)).toBe(true);
    expect([...layout.base].every((c) => c < 0x01000000)).toBe(true);
  });

  it("with health colours on, darkens the last column and row of a file big enough, so neighbours part", () => {
    const health = layoutMap(feed.files, { columns: 180, rows: 50, caseInsensitive: true, style: { theme: "dark", health: true } });
    const big = health.files.find((f) => f.px1 - f.px0 >= 3 && f.py1 - f.py0 >= 3)!;
    const fill = colorForScore(big.score);
    expect(health.base[big.py0 * 180 + big.px0]).toBe(fill);
    expect(health.base[big.py0 * 180 + big.px1 - 1]).toBe(mix(fill, 0, 0.3));
    expect(health.base[(big.py1 - 1) * 180 + big.px0]).toBe(mix(fill, 0, 0.3));
  });

  it("paints a missing score neutral", () => {
    expect(colorForScore(null)).toBe(parseInt(DARK_CANVAS.nodeNeutral.slice(1), 16));
  });

  it("labels wide groups inside their own cells, never two on one cell", () => {
    expect(layout.labels.map((l) => l.text)).toContain("django/db");
    const cells = layout.labels.flatMap((l) => [...l.text].map((_, k) => l.row * 180 + l.col + k));
    expect(new Set(cells).size).toBe(cells.length);
  });

  it("the legend counts exactly the files drawn", () => {
    const parts = scopeParts({ drawn: layout.drawn.length, shown: feed.files.length, repositoryTotal: feed.repository_total, indexed: "2h ago", beyondCap: 0, dense: layout.dense, notOnMap: 0 });
    expect(parts[0]).toBe(`${layout.drawn.length.toLocaleString("en-US")} of 2,970 files drawn at this size`);
  });
});

describe("labelText", () => {
  it("steps down from the folder to its last two segments to its last", () => {
    expect(labelText("tests/gis_tests/geoapp", 30)).toBe("tests/gis_tests/geoapp");
    expect(labelText("tests/gis_tests/geoapp", 18)).toBe("gis_tests/geoapp");
    expect(labelText("tests/gis_tests/geoapp", 8)).toBe("geoapp");
    expect(labelText("tests/gis_tests/geoapp", 3)).toBeNull();
  });

  it("refuses anything but printable ASCII, which is width 1 in every terminal", () => {
    expect(labelText("docs/日本語", 40)).toBeNull();
    expect(labelText("src/café", 40)).toBeNull();
  });
});

describe("a tiny canvas", () => {
  it("draws fewer files than it places, and counts only those", () => {
    const layout = layoutMap(feed.files, { columns: 12, rows: 4, caseInsensitive: true });
    expect(layout.drawn.length).toBeLessThan(layout.files.length);
    expect(distinctOwners(layout)).toBe(layout.drawn.length);
  });

  it("four equal files fill a 4x2 canvas", () => {
    const files: MapFile[] = ["a/1", "a/2", "a/3", "a/4"].map((p) => ({ file_path: p, score: 9, nloc: 1 }));
    const layout = layoutMap(files, { columns: 4, rows: 2, caseInsensitive: false });
    expect(layout.drawn).toHaveLength(4);
    expect([...layout.base].every((c) => c !== GROUND)).toBe(true);
  });
});

describe("cell encoding", () => {
  it("round-trips pixels through half blocks, labels aside, the empty ground left to the terminal", () => {
    const layout = { ...layoutMap(feed.files, { columns: 60, rows: 20, caseInsensitive: true }), labels: [] };
    const words = decode(encodeCells(layout, layout.base));
    expect(words).toHaveLength(60 * 20 * 3);
    for (let i = 0; i < 60 * 20; i++) {
      const [r, c] = [Math.floor(i / 60), i % 60];
      expect([words[i * 3], words[i * 3 + 1], words[i * 3 + 2]]).toEqual(halfCell(layout.base[2 * r * 60 + c]!, layout.base[(2 * r + 1) * 60 + c]!));
    }
    expect([...words].some((w, i) => i % 3 === 2 && w === TERMINAL_DEFAULT)).toBe(true);
  });

  it("a cell half on the ground draws the other half alone, on the terminal's own background", () => {
    const tile = 0x42906f;
    expect(halfCell(tile, 0x123456)).toEqual([0x2580, tile, 0x123456]);
    expect(halfCell(tile, GROUND)).toEqual([0x2580, tile, TERMINAL_DEFAULT]);
    expect(halfCell(GROUND, tile)).toEqual([0x2584, tile, TERMINAL_DEFAULT]);
    expect(halfCell(GROUND, GROUND)).toEqual([0x20, TERMINAL_DEFAULT, TERMINAL_DEFAULT]);
  });

  it("writes a label whole on ground cells, or not at all when a mark covers any of its cells", () => {
    const layout = layoutMap(feed.files, { columns: 180, rows: 50, caseInsensitive: true });
    const label = layout.labels[0]!;
    const text = (words: Uint32Array) => [...label.text].map((_, k) => String.fromCharCode(words[(label.row * 180 + label.col + k) * 3]!)).join("");
    expect(text(decode(encodeCells(layout, layout.base)))).toBe(label.text);
    const marked = new Uint8Array(layout.width * layout.height);
    marked[(2 * label.row + 1) * 180 + label.col + label.text.length - 1] = 1;
    expect(text(decode(encodeCells(layout, layout.base, marked)))).toBe("▀".repeat(label.text.length));
  });

  it("encodes a 180x50 frame to the documented size", () => {
    const layout = layoutMap(feed.files, { columns: 180, rows: 50, caseInsensitive: true });
    expect(encodeCells(layout, layout.base)).toHaveLength(144_000);
  });
});

describe("ghost tiles at both ends of scale", () => {
  const pal = MAP_PALETTES.dark;
  /** The tones a layout's pixels use, the empty ground aside. */
  const tones = (l: MapLayout) => new Set([...l.base].filter((c) => c !== GROUND));
  /** Every tile tone a file could carry, edges darkened included. */
  const ghost = new Set([pal.tileA, pal.tileB, mix(pal.tileA, 0, 0.3), mix(pal.tileB, 0, 0.3)]);

  it("a small repo (100 files, synthetic) at 120x24: a tile per file, in the two quiet tones only", () => {
    const small = layoutMap(syntheticTree(100).files, { columns: 120, rows: 24, caseInsensitive: true });
    expect(small.dense).toBe(false);
    for (const c of tones(small)) expect(ghost.has(c)).toBe(true);
    expect(small.drawn.length).toBeGreaterThanOrEqual(95);
  });

  it("Django at 180x100: still a tile per file; no health colour unless asked", () => {
    const roomy = layoutMap(feed.files, { columns: 180, rows: 100, caseInsensitive: true });
    expect(roomy.dense).toBe(false);
    for (const c of tones(roomy)) expect(ghost.has(c)).toBe(true);
  });

  it("a huge repo (10,000 files, synthetic; the 4,000 the server sends) at 120x24: folders as tiles, one tone each, no per-file shimmer", () => {
    const huge = layoutMap(syntheticTree(10_000).files.slice(0, 4_000), { columns: 120, rows: 24, caseInsensitive: true });
    expect(huge.dense).toBe(true);
    expect(tones(huge)).toEqual(new Set([pal.tileA, pal.tileB]));
    // A folder with no folders inside it is one tone throughout: its files do not alternate.
    const leaf = huge.folders.filter((f, i) => !huge.folders.some((g, j) => j > i && g.x0 >= f.x0 && g.x1 <= f.x1 && g.y0 >= f.y0 && g.y1 <= f.y1));
    const big = leaf.filter((f) => (f.x1 - f.x0) * (f.y1 - f.y0) >= 4);
    expect(big.length).toBeGreaterThan(10);
    for (const f of big) {
      const seen = new Set<number>();
      for (let y = f.y0; y < f.y1; y++) for (let x = f.x0; x < f.x1; x++) seen.add(huge.base[y * huge.width + x] as number);
      expect(seen.size).toBe(1);
    }
  });

  it("the light theme uses the light ramp's tones", () => {
    const light = layoutMap(feed.files, { columns: 120, rows: 24, caseInsensitive: true, style: { theme: "light", health: false } });
    expect(tones(light).has(MAP_PALETTES.light.tileA) || tones(light).has(MAP_PALETTES.light.tileB)).toBe(true);
    expect(tones(light).has(pal.tileA)).toBe(false);
  });

  it("folder names are faint, on the tile under them, and only on large regions, a few at most", () => {
    const layout = layoutMap(feed.files, { columns: 180, rows: 50, caseInsensitive: true });
    expect(layout.labels.length).toBeGreaterThan(0);
    expect(layout.labels.length).toBeLessThanOrEqual(8);
    const words = decode(encodeCells(layout, layout.base));
    const l = layout.labels[0]!;
    const cell = (l.row * 180 + l.col) * 3;
    expect(words[cell + 1]).toBe(pal.folder);
    expect([GROUND, TERMINAL_DEFAULT]).not.toContain(words[cell + 2]);
  });
});
