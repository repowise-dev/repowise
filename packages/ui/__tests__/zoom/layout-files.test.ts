import { describe, it, expect } from "vitest";
import { type Camera, worldRectToScreen } from "../../src/zoom/camera";
import { MAX_CHILDREN_DRAWN } from "../../src/zoom/constants";
import { drawScene } from "../../src/zoom/draw-tree";
import { packLayout } from "../../src/zoom/layout";
import {
  FILE_LEVEL_MIN_FILES,
  FOLDER_H,
  fileLevelLayout,
  fileSections,
  TILE_ASPECT,
  type TileChild,
} from "../../src/zoom/layout-files";
import { pickNode } from "../../src/zoom/pick";
import { buildScene } from "../../src/zoom/scene";
import type { ZoomPalette } from "../../src/zoom/theme";
import { countingContext, syntheticMap } from "./synthetic-map";

let rank = 0;
function file(id: string, health: number | null, hotspot = false): TileChild {
  return { id, sibling_rank: ++rank, importance: 0.5, kind: "file", name: `${id}.py`, health_score: health, is_hotspot: hotspot };
}
function folder(id: string, health: number | null): TileChild {
  return { id, sibling_rank: ++rank, importance: 0.9, kind: "folder", name: id, health_score: health };
}

/** The services folder from the mockup: 3 folders, 11 hotspots, 16 other files. */
function services(): TileChild[] {
  const kids: TileChild[] = [folder("c4", 8.4), folder("zoom", 9.4), folder("history", null)];
  for (let i = 0; i < 11; i++) kids.push(file(`hot${i}`, i === 3 ? null : 4 + ((i * 7) % 6), true));
  for (let i = 0; i < 16; i++) kids.push(file(`f${i}`, i % 5 === 0 ? null : 6 + ((i * 3) % 4)));
  return kids;
}

const ids = (xs: readonly TileChild[]): string[] => xs.map((x) => x.id);

describe("fileSections", () => {
  it("groups folders, then hotspots, then the other files", () => {
    const s = fileSections(services())!;
    expect(s.map((x) => x.kind)).toEqual(["folders", "hotspots", "files"]);
    expect(s.map((x) => x.members.length)).toEqual([3, 11, 16]);
  });

  it("sorts each section worst health first, unscored last", () => {
    for (const sec of fileSections(services())!) {
      const scores = sec.members.map((m) => m.health_score ?? Infinity);
      expect(scores).toEqual([...scores].sort((a, b) => a - b));
    }
    expect(ids(fileSections(services())![0]!.members)).toEqual(["c4", "zoom", "history"]);
  });

  it("breaks health ties by rank, so the order is stable", () => {
    const kids = Array.from({ length: 9 }, (_, i) => file(`t${i}`, 7));
    expect(ids(fileSections(kids.slice().reverse())![0]!.members)).toEqual(ids(kids));
  });

  it("leaves out empty sections and is null when the children are not mostly files", () => {
    const allFiles = Array.from({ length: 9 }, (_, i) => file(`a${i}`, 7));
    expect(fileSections(allFiles)!.map((x) => x.kind)).toEqual(["files"]);
    const few = Array.from({ length: FILE_LEVEL_MIN_FILES - 1 }, (_, i) => file(`b${i}`, 7));
    expect(fileSections(few)).toBeNull();
    const mixed = [...allFiles, ...Array.from({ length: 9 }, (_, i) => folder(`d${i}`, 7))];
    expect(fileSections(mixed)).toBeNull();
    // Past the draw cap the children would not all draw.
    const many = Array.from({ length: MAX_CHILDREN_DRAWN + 1 }, (_, i) => file(`m${i}`, 7));
    expect(fileSections(many)).toBeNull();
    // A plain pack child (no kind) is never file level.
    expect(fileSections([{ id: "x", sibling_rank: 1, importance: 1 }])).toBeNull();
  });
});

describe("fileLevelLayout", () => {
  it("is null for a parent that packs as cards", () => {
    expect(fileLevelLayout([folder("a", 1), file("b", 1)], 1.5)).toBeNull();
  });

  for (const aspect of [0.6, 1, 1.5, 3]) {
    it(`gives every file one tile size in an aligned grid inside the parent (aspect ${aspect})`, () => {
      const kids = services();
      const r = fileLevelLayout(kids, aspect)!;
      expect(r.size).toBe(kids.length);
      const tiles = kids.filter((k) => k.kind === "file").map((k) => r.get(k.id)!);
      for (const t of tiles) {
        expect(t.w).toBeCloseTo(tiles[0]!.w, 12);
        expect(t.h).toBeCloseTo(tiles[0]!.h, 12);
        // On screen (x stretched back by the parent aspect) a tile keeps its shape.
        expect((t.w * aspect) / t.h).toBeCloseTo(TILE_ASPECT, 9);
      }
      const lefts = new Set(tiles.map((t) => t.x.toFixed(9)));
      const cols = lefts.size;
      expect(cols).toBeGreaterThan(0);
      // Columns are shared across the sections: every row starts at the same left edge.
      expect(Math.min(...tiles.map((t) => t.x))).toBeCloseTo(Math.min(...[...r.values()].map((t) => t.x)), 9);
      for (const a of r.values()) {
        expect(a.x).toBeGreaterThanOrEqual(-1e-9);
        expect(a.y).toBeGreaterThanOrEqual(-1e-9);
        expect(a.x + a.w).toBeLessThanOrEqual(1 + 1e-9);
        expect(a.y + a.h).toBeLessThanOrEqual(1 + 1e-9);
      }
      const all = [...r.values()];
      for (let i = 0; i < all.length; i++) {
        for (let j = i + 1; j < all.length; j++) {
          const a = all[i]!;
          const b = all[j]!;
          expect(a.x < b.x + b.w - 1e-9 && b.x < a.x + a.w - 1e-9 && a.y < b.y + b.h - 1e-9 && b.y < a.y + a.h - 1e-9).toBe(false);
        }
      }
    });
  }

  it("uses more columns in a wider parent", () => {
    const cols = (aspect: number): number => {
      const r = fileLevelLayout(services(), aspect)!;
      return new Set([...r.entries()].filter(([id]) => id.startsWith("f")).map(([, t]) => t.x.toFixed(9))).size;
    };
    expect(cols(3)).toBeGreaterThan(cols(0.6));
  });

  it("stacks the sections in order, each in reading order, folders as taller cards", () => {
    const kids = services();
    const r = fileLevelLayout(kids, 1.5)!;
    const s = fileSections(kids)!;
    const order = s.flatMap((sec) => sec.members.map((m) => r.get(m.id)!));
    for (let i = 1; i < order.length; i++) {
      const a = order[i - 1]!;
      const b = order[i]!;
      expect(b.y > a.y + 1e-9 || (Math.abs(b.y - a.y) < 1e-9 && b.x > a.x)).toBe(true);
    }
    const lastFolder = r.get(s[0]!.members[2]!.id)!;
    const firstHot = r.get(s[1]!.members[0]!.id)!;
    expect(lastFolder.h / firstHot.h).toBeCloseTo(FOLDER_H, 9);
    // A header band sits between the sections.
    expect(firstHot.y).toBeGreaterThan(lastFolder.y + lastFolder.h + firstHot.h / 2);
  });

  it("is what packLayout returns for a mostly-files parent", () => {
    const kids = services();
    expect(packLayout(kids, 1.5)).toEqual(fileLevelLayout(kids, 1.5));
  });

  it("ignores sibling links and set-asides there: sections are sorted by health", () => {
    const kids = services();
    const links = [{ source_id: "f0", target_id: "hot0", edge_count: 50 }];
    expect(packLayout(kids, 1.5, {}, links, new Set(["c4"]))).toEqual(fileLevelLayout(kids, 1.5));
  });
});

describe("file tiles on the canvas", () => {
  const palette = new Proxy({}, { get: () => "black" }) as ZoomPalette;
  const VP = { w: 1600, h: 1000 };
  const scene = buildScene(syntheticMap());
  // A leaf folder of the synthetic map: ten files, the first a hotspot.
  const FOLDER = "f:0:1:3";

  function drawOn(id: string, px: number) {
    const r = scene.worldRects.get(id)!;
    const cam: Camera = { cx: r.x + r.w / 2, cy: r.y + r.h / 2, scale: px / r.w };
    const { ctx } = countingContext();
    const texts: string[] = [];
    const fill = ctx.fillText.bind(ctx);
    (ctx as unknown as { fillText: (s: string, x: number, y: number) => void }).fillText = (s, x, y) => {
      texts.push(s);
      fill(s, x, y);
    };
    const stats = drawScene(ctx, scene, cam, VP, palette, { selectedId: null, hoveredId: null, lowDetail: false });
    return { cam, stats, texts };
  }

  it("draws the section headers and a tile per file", () => {
    const { texts } = drawOn(FOLDER, 1500);
    expect(texts).toContain("Hotspots");
    expect(texts).toContain("Other files");
    expect(texts).toContain(" · 9");
    const folder = scene.nodes.get(FOLDER)!;
    for (const id of folder.children) expect(texts).toContain(scene.nodes.get(id)!.name);
  });

  it("hit-tests a tile at its world rect", () => {
    const { cam, stats } = drawOn(FOLDER, 1500);
    const id = scene.nodes.get(FOLDER)!.children[4]!;
    const s = worldRectToScreen(cam, VP, scene.worldRects.get(id)!);
    expect(pickNode(stats, s.x + s.w / 2, s.y + s.h / 2)?.id).toBe(id);
  });
});
