// The desktop map over the recorded Django health-map feed (test/django.ts).
import { describe, expect, it } from "vitest";
import { DARK_CANVAS_BAND } from "@repowise-dev/ui/brand";
import { initialTrail, reduceTrail, type TrailState } from "../src/model/trail";
import { GROUND, layoutMap } from "../src/views/map";
import { mapSize } from "../src/views/mapPane";
import { SVG_CHARS, desktopSize, mapSvg, svgPaneView } from "../src/views/mapSvg";
import { framePixels, resolveOverlay } from "../src/views/overlay";
import { feed, importers } from "./django";

const ROOT = "C:\\work\\django";
const scope = { drawn: 0, repositoryTotal: feed.repository_total, indexed: null, beyondCap: 0, cap: 4_000 };

/** The busiest trail the model keeps: 200 reads, a search, and an edit with its importers. */
function busy(): TrailState {
  let t = initialTrail;
  for (const f of feed.files.slice(0, 260)) t = reduceTrail(t, { type: "read", path: `c:/work/django/${f.file_path}`.toLowerCase() });
  t = reduceTrail(t, { type: "search", paths: feed.files.slice(300, 400).map((f) => `c:/work/django/${f.file_path}`.toLowerCase()) });
  t = reduceTrail(t, { type: "edit", path: "c:/work/django/django/db/models/query.py" });
  return reduceTrail(t, { type: "callers", edits: t.edits, callers: { status: "ready", paths: importers } });
}

describe("desktop map", () => {
  const size = desktopSize(mapSize({ bodyColumns: 400, placement: "dock", bodyRows: 200 }));
  const layout = layoutMap(feed.files, size.columns, size.rows, true);

  it("caps the size, and stays under the Svg limit on the Django feed, at rest and with the busiest trail", () => {
    expect(size).toEqual({ columns: 160, rows: 45 });
    expect(mapSvg(layout, resolveOverlay(layout, initialTrail, ROOT)).length).toBeLessThan(SVG_CHARS);
    expect(mapSvg(layout, resolveOverlay(layout, busy(), ROOT)).length).toBeLessThan(SVG_CHARS);
    expect(desktopSize({ columns: 80, rows: 20 })).toEqual({ columns: 80, rows: 20 });
  });

  it("draws the terminal's resting frame: every non-ground pixel of it, once, in its color", () => {
    const resolved = resolveOverlay(layout, busy(), ROOT);
    const source = mapSvg(layout, resolved);
    const { pixels } = framePixels(layout, resolved.overlay);
    const counted = new Map<string, number>();
    for (const [, color, d] of source.matchAll(/<path stroke="(#[0-9a-f]{6})" d="([^"]+)"\/>/g)) {
      const n = [...d!.matchAll(/h(\d+)/g)].reduce((sum, m) => sum + Number(m[1]), 0);
      counted.set(color!, (counted.get(color!) ?? 0) + n);
    }
    const expected = new Map<string, number>();
    for (const p of pixels) {
      if (p !== GROUND) expected.set(`#${p.toString(16).padStart(6, "0")}`, (expected.get(`#${p.toString(16).padStart(6, "0")}`) ?? 0) + 1);
    }
    expect(counted).toEqual(expected);
    expect(source).toContain(DARK_CANVAS_BAND.good.toLowerCase());
  });

  it("is deterministic, labels its groups as text, and escapes them", () => {
    const resolved = resolveOverlay(layout, initialTrail, ROOT);
    expect(mapSvg(layout, resolved)).toBe(mapSvg(layout, resolved));
    expect(mapSvg(layout, resolved)).toMatch(/<text x="\d+" y="[\d.]+">django\//);
    const odd = { ...layout, labels: [{ row: 0, col: 0, text: "a<b&c" }] };
    expect(mapSvg(odd, resolved)).toContain("<text x=\"0\" y=\"1.6\">a&lt;b&amp;c</text>");
  });

  it("the pane: the drawing with its alt text over the legend; a drawing over the limit says so instead", () => {
    const resolved = resolveOverlay(layout, initialTrail, ROOT);
    const view = svgPaneView(layout, resolved, { ...scope, drawn: layout.drawn.length });
    if (view.type !== "Box") throw new Error("expected a Box");
    expect(view.children[0]).toMatchObject({ type: "Svg", props: { alt: expect.stringMatching(/^Code health map: [\d,]+ files drawn/) } });
    const huge = layoutMap(feed.files, 512, 256, true);
    const over = svgPaneView(huge, resolveOverlay(huge, busy(), ROOT), scope);
    if (over.type !== "Box") throw new Error("expected a Box");
    expect(over.children[0]).toMatchObject({ type: "Text", children: ["Lens map has too much detail to draw here; the terminal map shows it"] });
  });
});
