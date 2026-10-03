// The desktop map over the recorded Django health-map feed (test/django.ts).
import { describe, expect, it } from "vitest";
import { DARK_CANVAS_BAND } from "@repowise-dev/ui/brand";
import { NO_STORY } from "../src/model/story";
import { GROUND, MAP_PALETTES, layoutMap } from "../src/views/map";
import { mapSize } from "../src/views/mapPane";
import { SVG_CHARS, desktopSize, mapSvg, svgPaneView } from "../src/views/mapSvg";
import { NO_LIT, framePixels, resolveLit, type Lit } from "../src/views/overlay";
import { feed, importers } from "./django";

const scope = { drawn: 0, shown: feed.files.length, repositoryTotal: feed.repository_total, indexed: null, beyondCap: 0, dense: false, notOnMap: 0 };
const parts = { story: NO_STORY, reach: null, scope };
const hex = (n: number) => `#${n.toString(16).padStart(6, "0")}`;

/** A busy turn: 260 files opened, 100 search matches, query.py edited with its importers. */
const busy: Lit = {
  hits: feed.files.slice(300, 400).map((f) => f.file_path),
  reads: feed.files.slice(0, 260).map((f) => f.file_path),
  named: [],
  importers,
  edits: ["django/db/models/query.py"],
  edit: "django/db/models/query.py",
  current: "django/db/models/query.py",
};

describe("desktop map", () => {
  const size = desktopSize(mapSize({ bodyColumns: 400, placement: "dock", bodyRows: 200 }));
  const layout = layoutMap(feed.files, { columns: size.columns, rows: size.rows, caseInsensitive: true });

  it("caps the size, and stays under the Svg limit on the Django feed, at rest and on a busy turn", () => {
    expect(size).toEqual({ columns: 160, rows: 45 });
    expect(mapSvg(layout, resolveLit(layout, NO_LIT)).length).toBeLessThan(SVG_CHARS);
    expect(mapSvg(layout, resolveLit(layout, busy)).length).toBeLessThan(SVG_CHARS);
    expect(desktopSize({ columns: 80, rows: 20 })).toEqual({ columns: 80, rows: 20 });
  });

  it("draws the terminal's resting frame: every non-ground pixel of it, once, in its color; no background of its own", () => {
    const overlay = resolveLit(layout, busy);
    const source = mapSvg(layout, overlay);
    const { pixels } = framePixels(layout, overlay);
    const counted = new Map<string, number>();
    for (const [, color, d] of source.matchAll(/<path stroke="(#[0-9a-f]{6})" d="([^"]+)"\/>/g)) {
      const n = [...d!.matchAll(/h(\d+)/g)].reduce((sum, m) => sum + Number(m[1]), 0);
      counted.set(color!, (counted.get(color!) ?? 0) + n);
    }
    const expected = new Map<string, number>();
    for (const p of pixels) if (p !== GROUND) expected.set(hex(p), (expected.get(hex(p)) ?? 0) + 1);
    expect(counted).toEqual(expected);
    expect(source).not.toContain("<rect");
    expect(source).toContain(hex(MAP_PALETTES.dark.tileA));
    expect(source).not.toContain(DARK_CANVAS_BAND.good.toLowerCase());
    const health = layoutMap(feed.files, { columns: size.columns, rows: size.rows, caseInsensitive: true, style: { theme: "dark", health: true } });
    expect(mapSvg(health, resolveLit(health, NO_LIT))).toContain(DARK_CANVAS_BAND.good.toLowerCase());
  });

  it("is deterministic, names folders faintly and the lit files beside them, and escapes text", () => {
    const overlay = resolveLit(layout, busy);
    expect(mapSvg(layout, overlay)).toBe(mapSvg(layout, overlay));
    expect(mapSvg(layout, overlay)).toMatch(new RegExp(`<text x="\\d+" y="[\\d.]+" fill="${hex(MAP_PALETTES.dark.folder)}">`));
    expect(mapSvg(layout, overlay)).toContain(`fill="${hex(MAP_PALETTES.dark.edit)}">query.py</text>`);
    const odd = { ...layout, labels: [{ row: 0, col: 0, text: "a<b&c" }] };
    expect(mapSvg(odd, resolveLit(odd, NO_LIT))).toContain(`<text x="0" y="1.6" fill="${hex(MAP_PALETTES.dark.folder)}">a&lt;b&amp;c</text>`);
  });

  it("the pane: the drawing with its alt text over the strip; a drawing over the limit says so instead", () => {
    const view = svgPaneView(layout, resolveLit(layout, NO_LIT), { ...parts, scope: { ...scope, drawn: layout.drawn.length } });
    if (view.type !== "Box") throw new Error("expected a Box");
    expect(view.children[0]).toMatchObject({ type: "Svg", props: { alt: expect.stringMatching(/^Map of the repo: [\d,]+ files drawn as tiles/) } });
    const huge = layoutMap(feed.files, { columns: 512, rows: 256, caseInsensitive: true, style: { theme: "dark", health: true } });
    const over = svgPaneView(huge, resolveLit(huge, busy), parts);
    if (over.type !== "Box") throw new Error("expected a Box");
    expect(over.children[0]).toMatchObject({ type: "Text", children: ["Lens map has too much detail to draw here; the terminal map shows it"] });
  });
});
