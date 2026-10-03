import { describe, expect, it } from "vitest";
import { initialTrail, reduceTrail, type TrailState } from "../src/model/trail";
import { ACCENT, GROUND, layoutMap, mix, type MapLayout, type PlacedFile } from "../src/views/map";
import { EDIT_TINT, frameCells, framePixels, resolveOverlay, rippleRadius } from "../src/views/overlay";
import { feed, importers } from "./django";

const ROOT = "C:\\Users\\dev\\django";
const ROOT_KEY = "c:/users/dev/django";
const QUERY = "django/db/models/query.py";
const layout = layoutMap(feed.files, 180, 50, true);

/** The trail after reading these repo files in order, then editing one. */
function session(reads: string[], edit?: string): TrailState {
  let t = initialTrail;
  for (const r of reads) t = reduceTrail(t, { type: "read", path: `${ROOT_KEY}/${r}` });
  if (edit !== undefined) t = reduceTrail(t, { type: "edit", path: `${ROOT_KEY}/${edit}` });
  return t;
}

const withImporters = (t: TrailState) => reduceTrail(t, { type: "callers", edits: t.edits, callers: { status: "ready", paths: importers } });

function centerOf(l: MapLayout, i: number): number {
  const f = l.files[i]!;
  return Math.floor((f.py0 + f.py1 - 1) / 2) * l.width + Math.floor((f.px0 + f.px1 - 1) / 2);
}

const at = (f: PlacedFile, x: number, y: number) => y * layout.width + x;

describe("resolveOverlay", () => {
  const edited = withImporters(session(["django/db/models/base.py", "django/db/models/manager.py"], QUERY));
  const resolved = resolveOverlay(layout, edited, ROOT);

  it("the importer count shown equals the count fetched: drawn plus not drawn", () => {
    expect(importers).toHaveLength(12);
    expect(resolved.overlay.callers.length + resolved.edit!.notDrawn).toBe(importers.length);
    expect(resolved.edit!.name).toBe("query.py");
  });

  it("maps reads case-insensitively under the root and counts the rest as not drawn", () => {
    const t = reduceTrail(edited, { type: "read", path: "c:/elsewhere/notes.md" });
    const r = resolveOverlay(layout, t, ROOT);
    expect(r.overlay.reads).toHaveLength(3);
    expect(r.reads).toEqual({ count: 4, capped: false, notDrawn: 1 });
  });

  it("reports what the last search matched, drawn or not", () => {
    expect(resolveOverlay(layout, initialTrail, ROOT).matched).toBeNull();
    const t = reduceTrail(initialTrail, { type: "search", paths: [`${ROOT_KEY}/${QUERY}`, "c:/elsewhere/x.py"] });
    const r = resolveOverlay(layout, t, ROOT);
    expect(r.matched).toBe(2);
    expect(r.overlay.hits).toHaveLength(1);
  });

  it("is quiet before any attention", () => {
    const r = resolveOverlay(layout, initialTrail, ROOT);
    expect(r.edit).toBeNull();
    expect(framePixels(layout, r.overlay).pixels).toEqual(layout.base);
  });
});

describe("frames", () => {
  const edited = withImporters(session(["django/db/models/base.py", "django/db/models/manager.py"], QUERY));
  const { overlay } = resolveOverlay(layout, edited, ROOT);
  const rest = framePixels(layout, overlay).pixels;

  it("outlines a read in orange and dims its inside", () => {
    const f = layout.files[overlay.reads[0]!]!;
    expect(rest[at(f, f.px0, f.py0)]).toBe(ACCENT);
    const inside = at(f, f.px0 + 1, f.py0 + 1);
    expect(rest[inside]).toBe(mix(layout.base[inside]!, 0, 0.4));
  });

  it("fills the edited file pale orange inside an orange outline", () => {
    const q = layout.files[overlay.edited!]!;
    expect(rest[at(q, q.px0, q.py0)]).toBe(ACCENT);
    expect(rest[at(q, q.px0 + 1, q.py0 + 1)]).toBe(EDIT_TINT);
  });

  it("dots each importer with a dark companion pixel in its cell", () => {
    for (const i of overlay.callers) {
      const c = centerOf(layout, i);
      const y = Math.floor(c / layout.width);
      expect(rest[c]).toBe(ACCENT);
      expect(rest[(y ^ 1) * layout.width + (c % layout.width)]).toBe(GROUND);
    }
  });

  it("the ripple reaches importers as it passes and rests with every one dotted", () => {
    const dotted = (t: number) =>
      overlay.callers.filter((i) => framePixels(layout, overlay, { kind: "ripple", t }).pixels[centerOf(layout, i)] === ACCENT).length;
    const counts = [0, 0.25, 0.5, 0.75, 1].map(dotted);
    expect(counts[0]).toBeLessThan(overlay.callers.length);
    for (let k = 1; k < counts.length; k++) expect(counts[k]!).toBeGreaterThanOrEqual(counts[k - 1]!);
    expect(counts[4]).toBe(overlay.callers.length);
    expect(frameCells(layout, overlay, { kind: "ripple", t: 1 })).toBe(frameCells(layout, overlay));
  });

  it("draws a ring mid-ripple that is gone at rest", () => {
    const mid = framePixels(layout, overlay, { kind: "ripple", t: 0.5 }).pixels;
    expect(mid.filter((c, i) => c !== rest[i]).length).toBeGreaterThan(overlay.callers.length);
  });

  it("a search flashes, then settles to a faint tint", () => {
    const t = reduceTrail(initialTrail, { type: "search", paths: [`${ROOT_KEY}/${QUERY}`] });
    const r = resolveOverlay(layout, t, ROOT).overlay;
    const c = centerOf(layout, r.hits[0]!);
    expect(framePixels(layout, r, { kind: "flash", t: 0 }).pixels[c]).toBe(mix(layout.base[c]!, ACCENT, 0.85));
    expect(framePixels(layout, r, { kind: "flash", t: 1 }).pixels[c]).toBe(mix(layout.base[c]!, ACCENT, 0.25));
    expect(framePixels(layout, r).pixels[c]).toBe(mix(layout.base[c]!, ACCENT, 0.25));
  });

  it("nothing to ripple to: radius 0, whether the edit is off the map or has no importers", () => {
    const off = resolveOverlay(layout, withImporters(session([], "setup.py")), ROOT).overlay;
    expect(off.edited).toBeNull();
    expect(rippleRadius(layout, off)).toBe(0);
    const lonely = reduceTrail(session([], QUERY), { type: "callers", edits: 1, callers: { status: "ready", paths: [] } });
    expect(rippleRadius(layout, resolveOverlay(layout, lonely, ROOT).overlay)).toBe(0);
    expect(rippleRadius(layout, overlay)).toBeGreaterThan(3);
  });

  it("an edit off the map still dots its importers", () => {
    const off = resolveOverlay(layout, withImporters(session([], "setup.py")), ROOT).overlay;
    const pixels = framePixels(layout, off, { kind: "ripple", t: 0 }).pixels;
    expect(off.callers.every((c) => pixels[centerOf(layout, c)] === ACCENT)).toBe(true);
  });

  it("a small edited file is all pale, unlike a small read", () => {
    const small = layout.drawn.map((i) => layout.files[i]!).find((f) => f.px1 - f.px0 < 3 && f.path.startsWith("django/"))!;
    const rel = small.path;
    const asEdit = resolveOverlay(layout, session([], rel), ROOT).overlay;
    const asRead = resolveOverlay(layout, session([rel]), ROOT).overlay;
    const p = at(small, small.px0, small.py0);
    expect(framePixels(layout, asEdit).pixels[p]).toBe(EDIT_TINT);
    expect(framePixels(layout, asRead).pixels[p]).toBe(ACCENT);
  });
});

describe("performance", () => {
  // The budget is 16 ms per frame at 180x50 (npm run bench:map measures it);
  // this guard fails only at five times that, to stay steady under CI load.
  it("lays out Django and encodes a ripple frame within 5x the frame budget", () => {
    const trail = withImporters(session(["django/db/models/base.py"], QUERY));
    const runs = 10;
    const start = performance.now();
    for (let k = 0; k < runs; k++) {
      const l = layoutMap(feed.files, 180, 50, true);
      frameCells(l, resolveOverlay(l, trail, ROOT).overlay, { kind: "ripple", t: 0.5 });
    }
    expect((performance.now() - start) / runs).toBeLessThan(16 * 5);
  });
});
