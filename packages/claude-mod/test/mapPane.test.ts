import { describe, expect, it } from "vitest";
import { DARK_CANVAS_BAND } from "@repowise-dev/ui/brand";
import { NO_STORY, type Reach, type Story } from "../src/model/story";
import { HINTS, MAP_COPY, STORY_COPY, freshnessLine, linesPart, notPlacedLine, reachesPart, scopeParts, searchedPart } from "../src/views/copy";
import type { Node } from "../src/views/elements";
import { MAP_PALETTES, layoutMap } from "../src/views/map";
import { HEALTH_KEY, MAP_KEY, legendRows, mapPaneView, mapSize, noticeView, packRows, storyView, type MapPaneParts } from "../src/views/mapPane";
import { NO_LIT, frameCells, resolveLit } from "../src/views/overlay";
import { feed, importers } from "./django";

function texts(node: Node): string[] {
  if (node.type === "Text") return [node.children.join("")];
  if (node.type === "Button") return [node.props.label];
  if (node.type !== "Box") return [];
  return node.children.flatMap(texts);
}

/** One row as drawn: a Text, or a row Box's items, each item's texts run together, items `columnGap` apart. */
function line(node: Node): string {
  if (node.type !== "Box") return texts(node).join("");
  return node.children.map((c) => texts(c).join("")).join(" ".repeat(node.props.columnGap ?? 0));
}

function rows(node: Node): string[] {
  return node.type === "Box" ? node.children.map(line) : [];
}

const DARK = { theme: "dark" as const, health: false };
const hex = (n: number) => `#${n.toString(16).padStart(6, "0")}`;
const scope = { drawn: 1_639, shown: 2_347, repositoryTotal: 2_970, indexed: "2h ago", beyondCap: 0, dense: false, notOnMap: 0 };

/** The recorded Django turn's story: Flow's record of it, as model/story.ts reads it. */
const story: Story = {
  searched: [
    { pattern: "class QuerySet", hits: 4 },
    { pattern: "django/db/models/sql/*.py", hits: 7 },
  ],
  opened: [
    { name: "manager.py", namedBy: null },
    { name: "query.py", namedBy: "get_context" },
  ],
  edited: [{ name: "query.py", lines: { delta: 1 } }],
};
const reach: Reach = {
  edit: "django/db/models/query.py",
  callers: { status: "ready", paths: importers },
  opened: new Set(["django/db/models/manager.py", "django/db/models/query.py"]),
};

describe("mapSize and legendRows", () => {
  it("docked: full width, the pane's rows less the story, scope and toggle rows (and the health legend when on)", () => {
    expect(legendRows(180)).toBe(4 + 2 + 1);
    expect(legendRows(180, true)).toBe(4 + 1 + 2 + 1);
    expect(mapSize({ bodyColumns: 180, placement: "dock", bodyRows: 55 })).toEqual({ columns: 180, rows: 48 });
    expect(mapSize({ bodyColumns: 180, placement: "dock", bodyRows: 55 }, true)).toEqual({ columns: 180, rows: 47 });
  });

  it("inline: a squat share of the width, capped; within the Raster's limits", () => {
    expect(mapSize({ bodyColumns: 120, placement: "inline", bodyRows: 3 })).toEqual({ columns: 120, rows: 20 });
    expect(mapSize({ bodyColumns: 900, placement: "dock", bodyRows: 900 })).toEqual({ columns: 512, rows: 256 });
    expect(mapSize({ bodyColumns: 0, placement: "dock", bodyRows: 2 })).toEqual({ columns: 1, rows: 4 });
  });

  it("packRows breaks before an item that would pass the width", () => {
    expect(packRows(["aaa", "bb", "cccc"], (s) => s.length, 7, 2)).toEqual([["aaa", "bb"], ["cccc"]]);
    expect(packRows([], (s: string) => s.length, 7, 2)).toEqual([]);
  });
});

describe("the story strip", () => {
  it("names what the turn searched, opened, edited and reaches, with units", () => {
    expect(storyView(story, reach, DARK, 120).map(line)).toEqual([
      "SEARCHED  class QuerySet: 4 files · django/db/models/sql/*.py: 7 files",
      "OPENED    manager.py · query.py  ◆ query.py named by get_context",
      "EDITED    ◉ query.py +1 line",
      "REACHES   12 importers: manager.py (opened), fields.py, prefetch.py, +9",
    ]);
  });

  it("colors Repowise's naming and the importers plum, the edit amber; heads dim", () => {
    const json = JSON.stringify(storyView(story, reach, DARK, 120));
    expect(json).toContain(hex(MAP_PALETTES.dark.importer));
    expect(json).toContain(hex(MAP_PALETTES.dark.edit));
    const light = JSON.stringify(storyView(story, reach, { theme: "light", health: false }, 120));
    expect(light).toContain(hex(MAP_PALETTES.light.edit));
  });

  it("rows appear only once they have something to say; the lookup in flight or failed says so", () => {
    expect(storyView(NO_STORY, null, DARK, 120)).toEqual([]);
    expect(storyView({ ...NO_STORY, opened: [{ name: "a.py", namedBy: null }] }, null, DARK, 120).map(line)).toEqual(["OPENED    a.py"]);
    const pending = storyView(story, { ...reach, callers: { status: "loading" } }, DARK, 120).map(line);
    expect(pending.at(-1)).toBe("REACHES   finding the files that import query.py");
    expect(storyView(story, { ...reach, callers: { status: "failed" } }, DARK, 120).map(line).at(-1)).toBe("REACHES   import graph unavailable");
  });

  it("fits a narrow pane", () => {
    for (const row of storyView(story, reach, DARK, 40).map(line)) expect(row.length).toBeLessThanOrEqual(40);
  });

  it("copy pieces carry their units", () => {
    expect(searchedPart("", 3)).toBe("last search matched 3 files");
    expect(searchedPart("x", null)).toBe("x: matches not counted");
    expect([linesPart(null), linesPart({ delta: -3 }), linesPart({ delta: 0 }), linesPart({ written: 40 })]).toEqual([
      "",
      " -3 lines",
      " 0 lines",
      " 40 lines written",
    ]);
    expect(reachesPart(1, [{ name: "a.py", opened: false }])).toBe("1 importer: a.py");
    expect(Object.values(STORY_COPY)).toEqual(["SEARCHED", "OPENED", "EDITED", "REACHES"]);
  });
});

describe("scope", () => {
  it("says what is drawn: all, drawn at this size, as folders, or the largest under the cap", () => {
    expect(scopeParts({ ...scope, drawn: 2_914, repositoryTotal: 2_914 })).toEqual(["2,914 files, all drawn", "indexed 2h ago"]);
    expect(scopeParts({ ...scope, indexed: null })).toEqual(["1,639 of 2,970 files drawn at this size", "rest too small to draw"]);
    expect(scopeParts({ ...scope, indexed: null, dense: true })).toEqual(["2,970 files, drawn as folders at this size"]);
    // Under the cap, never more tiles implied than are drawn: at both densities, and when all of the largest fit.
    const capped = { ...scope, indexed: null, shown: 4_000, repositoryTotal: 10_312, beyondCap: 6_312 };
    expect(scopeParts(capped)).toEqual(["1,639 of the 4,000 largest (of 10,312 files) drawn at this size", "rest too small to draw"]);
    expect(scopeParts({ ...capped, dense: true })).toEqual(["4,000 largest of 10,312 files; drawn as folders at this size"]);
    expect(scopeParts({ ...capped, drawn: 4_000 })).toEqual(["4,000 largest of 10,312 files drawn"]);
    expect(scopeParts({ ...scope, indexed: null, dense: true, notOnMap: 2 })).toEqual(["2,970 files, drawn as folders at this size", "2 touched files not on the map"]);
  });

  it("no em dash in anything the map writes", () => {
    const all = [...Object.values(HINTS), ...Object.values(MAP_COPY), notPlacedLine("below 110 columns (80 now)"), freshnessLine({ changedFiles: 3 }), ...scopeParts(scope)];
    for (const l of all) expect(l).not.toMatch(/[–—]/);
  });
});

describe("mapPaneView", () => {
  const layout = layoutMap(feed.files, { columns: 120, rows: 20, caseInsensitive: true });
  const view = (parts: MapPaneParts = { story, reach, scope }, l = layout) => mapPaneView(l, frameCells(l, resolveLit(l, NO_LIT)), parts);

  it("draws the Raster, then the strip as the legend, the scope, and the health toggle; no swatch row", () => {
    const tree = view();
    if (tree.type !== "Box") throw new Error("expected a Box");
    expect(tree.children[0]).toMatchObject({ type: "Raster", props: { key: MAP_KEY, columns: 120, rows: 20 } });
    const shown = rows(tree);
    expect(shown.slice(1, 5).map((r) => r.slice(0, 8))).toEqual(["SEARCHED", "OPENED  ", "EDITED  ", "REACHES "]);
    expect(shown.at(-2)).toBe("1,639 of 2,970 files drawn at this size · rest too small to draw · indexed 2h ago");
    expect(tree.children.at(-1)).toMatchObject({ type: "Button", props: { key: HEALTH_KEY, hotkey: "h", label: MAP_COPY.healthOff } });
    expect(JSON.stringify(tree)).not.toContain(DARK_CANVAS_BAND.at_risk);
  });

  it("quiet when nothing happened this turn: one line saying what will light up", () => {
    expect(rows(view({ story: NO_STORY, reach: null, scope }))[1]).toBe(MAP_COPY.quiet);
  });

  it("with health colours on, the band legend comes back and the toggle says on", () => {
    const health = layoutMap(feed.files, { columns: 120, rows: 20, caseInsensitive: true, style: { theme: "dark", health: true } });
    const tree = view({ story, reach, scope }, health);
    expect(rows(tree)).toContain("■ At risk  ■ Needs work  ■ Fair  ■ Good  ■ Excellent  ■ Not scored");
    expect(texts(tree).at(-1)).toBe(MAP_COPY.healthOn);
  });

  it("wraps the scope onto two rows when narrow; the cap clause survives, the rest note and the age leave first", () => {
    const capped = { ...scope, drawn: 1_500, shown: 4_000, repositoryTotal: 8_281, beyondCap: 4_281, notOnMap: 3 };
    const narrow = rows(view({ story, reach, scope: capped }, { ...layout, columns: 70 }));
    expect(narrow.slice(-3, -1)).toEqual(["1,500 of the 4,000 largest (of 8,281 files) drawn at this size", "indexed 2h ago · 3 touched files not on the map"]);
    const dense = rows(view({ story, reach, scope: { ...capped, dense: true } }, { ...layout, columns: 70 }));
    expect(dense.slice(-3, -1)).toEqual(["4,000 largest of 8,281 files; drawn as folders at this size", "indexed 2h ago · 3 touched files not on the map"]);
  });

  it("noticeView is one dim line, fitted", () => {
    expect(texts(noticeView(MAP_COPY.loading, 20))).toEqual(["Lens map: loading t…"]);
  });
});
