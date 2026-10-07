// The inspector's cursor and detail line, and zoom,
// over the recorded Django feed and blast radius (test/django.ts).
import { describe, expect, it } from "vitest";
import { initialFlow, type FlowState } from "../src/model/flow";
import { KNOWS_NOTHING, knowsOf, litPaths, step, whyLit } from "../src/model/inspect";
import { NO_STORY } from "../src/model/story";
import { crumbLine, detailLine } from "../src/views/copy";
import type { Node } from "../src/views/elements";
import { MAP_PALETTES, folderAt, layoutMap } from "../src/views/map";
import { mapPaneView } from "../src/views/mapPane";
import { NO_LIT, framePixels, frameCells, litPixels, resolveLit, type Lit } from "../src/views/overlay";
import { decode, feed, importers } from "./django";
import { syntheticTree } from "./synthetic";

const QUERY = "django/db/models/query.py";
const MANAGER = "django/db/models/manager.py";
const BASE = "django/db/models/base.py";
const pal = MAP_PALETTES.dark;
const lit: Lit = { hits: ["tests/queries/tests.py"], reads: [MANAGER, QUERY], named: [QUERY], importers, edits: [QUERY], edit: QUERY, current: QUERY };
const story = { ...NO_STORY, edited: [{ name: "query.py", lines: { delta: 1 } as const }] };

describe("folder mode", () => {
  const dense = layoutMap(feed.files, { columns: 110, rows: 24, caseInsensitive: true });
  const o = resolveLit(dense, lit);

  it("importers keep their solid plum fill, with nothing written on it", () => {
    expect(dense.dense).toBe(true);
    const onMap = importers.filter((p) => dense.index.has(p.toLowerCase())).length;
    expect(o.importers).toHaveLength(onMap);
    const { pixels } = framePixels(dense, o);
    const opened = new Set(lit.reads.map((p) => p.toLowerCase()));
    const plum = o.importers.filter((i) => !opened.has(dense.files[i]!.path.toLowerCase()));
    expect(plum.length).toBeGreaterThan(0);
    for (const i of plum) {
      const b = litPixels(dense, dense.files[i]!);
      expect(pixels[b.y0 * dense.width + b.x0]).toBe(pal.importer);
    }
    const words = decode(frameCells(dense, o));
    const i = plum[0]!;
    const b = litPixels(dense, dense.files[i]!);
    expect(String.fromCharCode(words[((b.y0 >> 1) * dense.columns + b.x0) * 3]!)).toMatch(/[▀▄ ]/);
  });

  it("folderAt names the deepest folder drawn at a pixel (a gutter belongs to its group)", () => {
    const f = dense.files[dense.index.get(BASE)!]!;
    const b = litPixels(dense, f);
    expect(folderAt(dense, b.x0, b.y0)).toMatch(/^django\/db\/models/);
    expect(folderAt(dense, dense.width + 5, 0)).toBeNull();
  });
});

describe("the inspector", () => {
  it("visits the edit, then the opened files, then the other importers in the graph's order, then named files, each once", () => {
    const order = litPaths(lit);
    expect(order[0]).toBe(QUERY);
    expect(order[1]).toBe(MANAGER);
    expect(order.slice(2)).toEqual(importers.filter((p) => p !== MANAGER));
    expect(new Set(order.map((p) => p.toLowerCase())).size).toBe(order.length);
  });

  it("steps forward and back and wraps around; from nothing it starts at either end", () => {
    const order = litPaths(lit);
    expect(step(order, null, 1)).toBe(order[0]);
    expect(step(order, null, -1)).toBe(order.at(-1));
    expect(step(order, order.at(-1)!, 1)).toBe(order[0]);
    expect(step(order, order[0]!, -1)).toBe(order.at(-1));
    expect(step(order, "not/lit.py", 1)).toBe(order[0]);
    expect(step([], null, 1)).toBeNull();
  });

  it("the detail line: the path, why it is lit, and what the index already said", () => {
    const knows = { context: { callerFiles: 131, contributors: 172, hotspot: true, recentOwner: null }, tests: { total: 9, basis: "inferred" as const }, namedBy: "get_context" };
    expect(detailLine(QUERY, whyLit(QUERY, lit, story, knows), knows)).toBe(
      "django/db/models/query.py · Claude edited it, +1 line · named by get_context · hotspot · 131 files use it · 9 tests reach it (inferred)",
    );
    expect(detailLine(MANAGER, whyLit(MANAGER, lit, story, KNOWS_NOTHING), KNOWS_NOTHING)).toBe("django/db/models/manager.py · Claude opened it · imports query.py");
    expect(detailLine(BASE, whyLit(BASE, lit, story, KNOWS_NOTHING), KNOWS_NOTHING)).toBe("django/db/models/base.py · imports query.py");
  });

  it("what Lens knows comes from what it already holds: the file card, the edit's tests, the naming reply", () => {
    const card = { callerFiles: 3, contributors: 2, hotspot: false, recentOwner: null };
    const flow: FlowState = {
      ...initialFlow,
      blast: { [QUERY]: { importers: [], cochange: [], tests: { total: 4, files: [], basis: "measured" } } },
      named: { [QUERY]: { id: "toolu_rw", tool: "get_context", at: 1, turn: 1 } },
    };
    expect(knowsOf("Django/DB/models/query.py", { [QUERY]: card }, flow)).toEqual({ context: card, tests: { total: 4, basis: "measured" }, namedBy: "get_context" });
    expect(knowsOf(QUERY, {}, null)).toEqual(KNOWS_NOTHING);
  });

  it("the selected tile is ringed in the theme's brightest neutral, just outside it", () => {
    const layout = layoutMap(feed.files, { columns: 180, rows: 60, caseInsensitive: true });
    const o = resolveLit(layout, lit, BASE);
    const b = litPixels(layout, layout.files[o.selected!]!);
    const { pixels } = framePixels(layout, o);
    expect(pixels[(b.y0 - 1) * layout.width + b.x0]).toBe(pal.ring);
    expect(pixels[b.y0 * layout.width + b.x0]).toBe(pal.importer);
  });
});

describe("zoom", () => {
  const texts = (n: Node): string[] => (n.type === "Text" ? [n.children.join("")] : n.type === "Box" ? n.children.flatMap(texts) : []);

  it("lays out only the folder's files at the full size, named relative to it, with file names on big tiles", () => {
    const zoom = layoutMap(feed.files, { columns: 110, rows: 24, caseInsensitive: true, root: "django/db/models" });
    expect(zoom.root).toBe("django/db/models");
    expect(zoom.files.every((f) => f.path.startsWith("django/db/models/"))).toBe(true);
    expect(zoom.dense).toBe(false);
    expect(zoom.labels.some((l) => l.text === "query.py" || l.text === "__init__.py")).toBe(true);
    expect(zoom.labels.every((l) => !l.text.startsWith("django/"))).toBe(true);
  });

  it("keeps the turn's lighting, and shows where it is above the drawing", () => {
    const zoom = layoutMap(feed.files, { columns: 110, rows: 24, caseInsensitive: true, root: "django/db/models" });
    const o = resolveLit(zoom, lit, BASE);
    expect(o.edit).not.toBeNull();
    expect(o.importers.length).toBeGreaterThan(0);
    const view = mapPaneView(zoom, frameCells(zoom, o), { story: NO_STORY, reach: null, scope: { drawn: 1, shown: 1, repositoryTotal: 1, indexed: null, beyondCap: 0, dense: false, notOnMap: 3, zoom: zoom.root } });
    expect(texts(view)[0]).toBe("django / db / models");
    expect(texts(view)).toContain("1 file, all drawn · 3 touched files outside this folder");
    expect(crumbLine("a/b")).toBe("a / b");
  });

  it("works at both ends of scale: a 10,000-file tree (synthetic) zooms into one top folder", () => {
    const tree = syntheticTree(10_000).files.slice(0, 4_000);
    const zoom = layoutMap(tree, { columns: 120, rows: 24, caseInsensitive: true, root: "src" });
    expect(zoom.files.length).toBeGreaterThan(100);
    expect(zoom.files.every((f) => f.path.startsWith("src/"))).toBe(true);
    expect(resolveLit(zoom, NO_LIT).names).toEqual([]);
  });
});
