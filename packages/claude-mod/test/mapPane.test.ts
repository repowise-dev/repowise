import { describe, expect, it } from "vitest";
import { BRAND, DARK_CANVAS_BAND } from "@repowise-dev/ui/brand";
import { initialTrail, reduceTrail, type TrailState } from "../src/model/trail";
import { HINTS, MAP_COPY, callersLine, freshnessLine, notPlacedLine, readsLine, scopeParts } from "../src/views/copy";
import type { Node } from "../src/views/elements";
import { layoutMap } from "../src/views/map";
import { MAP_KEY, legendRows, mapPaneView, mapSize, noticeView, packRows } from "../src/views/mapPane";
import { frameCells, resolveOverlay } from "../src/views/overlay";
import { feed } from "./django";

function texts(node: Node): string[] {
  if (node.type === "Text") return [node.children.join("")];
  if (node.type === "Raster") return [];
  if (node.type === "Button") return [node.props.label];
  return node.children.flatMap(texts);
}

/** One row as drawn: a Text, or a row Box's items, each item's texts run together, items two apart. */
function line(node: Node): string {
  if (node.type !== "Box") return texts(node).join("");
  return node.children.map((c) => texts(c).join("")).join(" ".repeat(node.props.columnGap ?? 0));
}

function rows(node: Node): string[] {
  return node.type === "Box" ? node.children.map(line) : [];
}

const scope = { drawn: 1_639, repositoryTotal: 2_970, indexed: "2h ago", beyondCap: 0, cap: 4_000 };

describe("mapSize and legendRows", () => {
  it("docked: full width, the pane's rows less the legend at that width", () => {
    expect(legendRows(180)).toBe(6);
    expect(mapSize({ bodyColumns: 180, placement: "dock", bodyRows: 55 })).toEqual({ columns: 180, rows: 49 });
  });

  it("a narrow pane wraps the swatches and reserves the rows for them", () => {
    expect(legendRows(40)).toBe(9);
    expect(mapSize({ bodyColumns: 40, placement: "dock", bodyRows: 30 })).toEqual({ columns: 40, rows: 21 });
  });

  it("inline: a squat share of the width, capped", () => {
    expect(mapSize({ bodyColumns: 120, placement: "inline", bodyRows: 3 })).toEqual({ columns: 120, rows: 20 });
    expect(mapSize({ bodyColumns: 60, placement: "inline", bodyRows: 3 })).toEqual({ columns: 60, rows: 10 });
  });

  it("stays within the Raster's limits", () => {
    expect(mapSize({ bodyColumns: 900, placement: "dock", bodyRows: 900 })).toEqual({ columns: 512, rows: 256 });
    expect(mapSize({ bodyColumns: 0, placement: "dock", bodyRows: 2 })).toEqual({ columns: 1, rows: 4 });
  });

  it("packRows breaks before an item that would pass the width", () => {
    expect(packRows(["aaa", "bb", "cccc"], (s) => s.length, 7, 2)).toEqual([["aaa", "bb"], ["cccc"]]);
    expect(packRows([], (s: string) => s.length, 7, 2)).toEqual([]);
  });
});

describe("copy", () => {
  it("scope: all drawn, or how many at this size, then age and any cap", () => {
    expect(scopeParts({ ...scope, drawn: 2_914, repositoryTotal: 2_914 })).toEqual(["2,914 files, all drawn", "indexed 2h ago"]);
    expect(scopeParts({ ...scope, indexed: null, beyondCap: 1_200 })).toEqual([
      "1,639 of 2,970 files drawn at this size",
      "rest empty or too small",
      "1,200 beyond the 4,000-file cap",
    ]);
  });

  it("reads: nothing before the first, then reads, what was not drawn, and the last search", () => {
    const r = (count: number, notDrawn = 0, capped = false) => ({ count, capped, notDrawn });
    expect(readsLine(r(0), null)).toBeNull();
    expect(readsLine(r(1), null)).toBe("1 file read");
    expect(readsLine(r(8, 3), null)).toBe("8 files read · 3 not drawn");
    expect(readsLine(r(200, 0, true), 14)).toBe("200+ files read · last search matched 14 files");
    expect(readsLine(r(0), 0)).toBe("last search matched 0 files");
  });

  it("importers: loading, unavailable, found, some not drawn", () => {
    expect(callersLine("query.py", null, 0)).toBe("edited query.py · finding the files that import it");
    expect(callersLine("query.py", { status: "loading" }, 0)).toBe("edited query.py · finding the files that import it");
    expect(callersLine("query.py", { status: "failed" }, 0)).toBe("edited query.py · import graph unavailable");
    expect(callersLine("query.py", { status: "ready", paths: ["a"] }, 0)).toBe("edited query.py · 1 file imports it (from imports, not calls)");
    expect(callersLine("query.py", { status: "ready", paths: new Array(12).fill("x") }, 3)).toBe(
      "edited query.py · 12 files import it (from imports, not calls) · 3 not drawn",
    );
  });

  it("no em dash in anything Lens writes", () => {
    const all = [
      ...Object.values(HINTS),
      ...Object.values(MAP_COPY),
      notPlacedLine("below 110 columns (80 now)"),
      freshnessLine({ changedFiles: 3 }),
      ...scopeParts({ ...scope, beyondCap: 5 }),
      readsLine({ count: 3, capped: true, notDrawn: 1 }, 2),
      callersLine("a.py", { status: "ready", paths: ["b"] }, 1),
      callersLine("a.py", { status: "failed" }, 0),
      callersLine("a.py", null, 0),
    ];
    for (const line of all) expect(line).not.toMatch(/[\u2013\u2014]/);
  });
});

describe("mapPaneView", () => {
  const layout = layoutMap(feed.files, 120, 20, true);
  const root = "C:\\work\\django";
  const view = (t: TrailState, columns = 120) => {
    const resolved = resolveOverlay(layout, t, root);
    return mapPaneView({ ...layout, columns }, frameCells(layout, resolved.overlay), resolved, scope);
  };

  it("draws the Raster at the layout's size and the legend outside it, with every band word", () => {
    const tree = view(initialTrail);
    if (tree.type !== "Box") throw new Error("expected a Box");
    expect(tree.children[0]).toMatchObject({ type: "Raster", props: { key: MAP_KEY, columns: 120, rows: 20 } });
    expect(rows(tree)).toEqual([
      "",
      "■ At risk  ■ Needs work  ■ Fair  ■ Good  ■ Excellent  ■ Not scored",
      "□ Claude read  ◆ edited",
      "1,639 of 2,970 files drawn at this size · rest empty or too small · indexed 2h ago",
    ]);
  });

  it("colors swatches with the canvas band ramp and Claude's marks orange", () => {
    const json = JSON.stringify(view(initialTrail));
    expect(json).toContain(DARK_CANVAS_BAND.at_risk);
    expect(json).toContain(DARK_CANVAS_BAND.excellent);
    expect(json).toContain(BRAND.accent);
  });

  it("names the importer and match marks only when they are on the map, and states the trail", () => {
    let t = reduceTrail(initialTrail, { type: "read", path: "c:/work/django/django/db/models/base.py" });
    t = reduceTrail(t, { type: "read", path: "c:/elsewhere/x.py" });
    t = reduceTrail(t, { type: "search", paths: ["c:/work/django/django/db/models/base.py"] });
    t = reduceTrail(t, { type: "edit", path: "c:/work/django/django/db/models/query.py" });
    t = reduceTrail(t, { type: "callers", edits: 1, callers: { status: "ready", paths: ["django/db/models/base.py", "not/in/feed.py"] } });
    const shown = rows(view(t));
    expect(shown).toContain("□ Claude read  ◆ edited  · imports the edited file  ▪ search match");
    expect(shown).toContain("3 files read · 1 not drawn · last search matched 1 file");
    expect(shown).toContain("edited query.py · 2 files import it (from imports, not calls) · 1 not drawn");
  });

  it("wraps the scope onto a second row when narrow, so the age survives", () => {
    const shown = rows(view(initialTrail, 50));
    expect(shown.slice(-2)).toEqual(["1,639 of 2,970 files drawn at this size", "rest empty or too small · indexed 2h ago"]);
  });

  it("noticeView is one dim line, fitted", () => {
    expect(texts(noticeView(MAP_COPY.loading, 20))).toEqual(["Lens map: loading t…"]);
  });
});
