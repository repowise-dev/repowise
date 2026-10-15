/**
 * The field's geometry and picking, as pure functions: it must read for a
 * five-file repository and for an eight-thousand-file one, and stay cheap.
 */
import { describe, it, expect } from "vitest";
import {
  MAX_GALAXIES,
  WORLD,
  fitCamera,
  galaxyKeyFor,
  layoutField,
} from "../../src/health/map/layout.js";
import { buildHitGrid, hitTest } from "../../src/health/map/hit-test.js";
import { buildScene, groupBatches, parseCssColour } from "../../src/health/map/paint.js";
import { OVERLAY_SPECS } from "../../src/health/map/lens.js";
import type { CodeHealthMapFile } from "../../src/health/map/types.js";
import { mapFile as f } from "./map-test-utils.js";

/** A synthetic repository: `n` files over `modules` modules, sizes skewed. */
function synthetic(n: number, modules: number): CodeHealthMapFile[] {
  return Array.from({ length: n }, (_, i) => {
    const m = i % modules;
    return f(`pkg${m}/sub${i % 7}/file${i}.py`, 5 + ((i * 7919) % 900), `pkg${m}`, {
      score: 1 + ((i * 31) % 90) / 10,
    });
  });
}

const allNodes = (files: CodeHealthMapFile[]) => layoutField(files).flatMap((g) => g.nodes);

describe("layout for any size", () => {
  it("is deterministic", () => {
    const files = synthetic(500, 9);
    expect(layoutField(files)).toEqual(layoutField([...files].reverse()));
  });

  it("keeps every node inside the world square", () => {
    for (const n of allNodes(synthetic(2000, 30))) {
      expect(n.x - n.r).toBeGreaterThanOrEqual(-1e-6);
      expect(n.y - n.r).toBeGreaterThanOrEqual(-1e-6);
      expect(n.x + n.r).toBeLessThanOrEqual(WORLD + 1e-6);
      expect(n.y + n.r).toBeLessThanOrEqual(WORLD + 1e-6);
    }
  });

  it("caps node size, so a tiny repository draws dots and not plates", () => {
    const files = [f("a.py", 400, "src"), f("b.py", 90, "src"), f("c.py", 12, "src")];
    const cam = fitCamera(900, 720);
    for (const n of allNodes(files)) expect(n.r * cam.scale).toBeLessThanOrEqual(18);
  });

  it("gives a file the same size in any galaxy", () => {
    const files = [
      f("big/a.py", 100, "big"),
      ...Array.from({ length: 50 }, (_, i) => f(`big/x${i}.py`, 300, "big")),
      f("small/b.py", 100, "small"),
    ];
    const nodes = allNodes(files);
    const a = nodes.find((n) => n.file.file_path === "big/a.py")!;
    const b = nodes.find((n) => n.file.file_path === "small/b.py")!;
    expect(a.r).toBeCloseTo(b.r, 9);
  });

  it("never lets two dots overlap", () => {
    // Circle packing guarantees it, so any overlap at all is a regression.
    const nodes = allNodes(synthetic(400, 4));
    let overlaps = 0;
    for (let i = 0; i < nodes.length; i++) {
      for (let j = i + 1; j < nodes.length; j++) {
        const a = nodes[i]!;
        const b = nodes[j]!;
        if (Math.hypot(a.x - b.x, a.y - b.y) < a.r + b.r - 1e-6) overlaps++;
      }
    }
    expect(overlaps).toBe(0);
  });

  it("groups one module by sub-folder so it separates into galaxies", () => {
    const files = ["api", "core", "db", "ui", "cli"].flatMap((area) =>
      Array.from({ length: 6 }, (_, i) => f(`src/${area}/f${i}.py`, 40, "src")),
    );
    const galaxies = layoutField(files);
    expect(galaxies.map((g) => g.module).sort()).toEqual([
      "src/api",
      "src/cli",
      "src/core",
      "src/db",
      "src/ui",
    ]);
  });

  it("keeps the module grouping when there are enough modules", () => {
    const key = galaxyKeyFor(synthetic(100, 6));
    expect(key(f("pkg3/sub1/x.py", 10, "pkg3"))).toBe("pkg3");
  });

  it("folds the smallest of hundreds of modules, so galaxies stay readable", () => {
    const files = [
      ...Array.from({ length: 30 }, (_, m) =>
        Array.from({ length: 40 }, (_, i) => f(`apps/big${m}/f${i}.py`, 50, `apps/big${m}`)),
      ).flat(),
      ...Array.from({ length: 300 }, (_, m) => f(`libs/tiny${m}/x.py`, 20, `libs/tiny${m}`)),
    ];
    const galaxies = layoutField(files);
    expect(galaxies.length).toBeLessThanOrEqual(MAX_GALAXIES);
    const fold = galaxies.find((g) => g.members > 1);
    expect(fold?.label).toMatch(/modules$/);
    expect(galaxies.reduce((n, g) => n + g.fileCount, 0)).toBe(files.length);
  });
});

describe("hit testing", () => {
  const nodes = allNodes(synthetic(3000, 12));
  const grid = buildHitGrid(nodes);

  it("finds the node under its own centre", () => {
    for (const i of [0, 17, 999, 2999]) {
      const n = nodes[i]!;
      expect(hitTest(grid, n.x, n.y)).toBe(i);
    }
  });

  it("finds nothing in empty space and something within the slop", () => {
    expect(hitTest(grid, -50, -50)).toBe(-1);
    // Packed dots sit close, so probe each side of node 5 for a point just
    // outside it that no neighbour is within the slop of.
    const n = nodes[5]!;
    const clear = [0, 1, 2, 3, 4, 5, 6, 7]
      .map((k) => (k * Math.PI) / 4)
      .map((a) => ({ x: n.x + (n.r + 0.5) * Math.cos(a), y: n.y + (n.r + 0.5) * Math.sin(a) }))
      .find((p) =>
        nodes.every((m, i) => i === 5 || Math.hypot(p.x - m.x, p.y - m.y) - m.r > 1),
      );
    expect(clear).toBeDefined();
    expect(hitTest(grid, clear!.x, clear!.y, 0)).not.toBe(5);
    expect(hitTest(grid, clear!.x, clear!.y, 1)).toBe(5);
  });

  it("agrees with a brute-force scan", () => {
    for (let k = 0; k < 200; k++) {
      const x = (k * 37) % WORLD;
      const y = (k * 61) % WORLD;
      let brute = -1;
      let best = Infinity;
      nodes.forEach((n, i) => {
        const gap = Math.hypot(x - n.x, y - n.y) - n.r;
        if (gap <= 0 && gap < best) {
          brute = i;
          best = gap;
        }
      });
      expect(hitTest(grid, x, y)).toBe(brute);
    }
  });
});

describe("colour parsing", () => {
  it("reads the forms a computed style returns", () => {
    expect(parseCssColour("rgb(10, 20, 30)")).toEqual([10, 20, 30]);
    expect(parseCssColour("rgba(10, 20, 30, 0.5)")).toEqual([10, 20, 30]);
    expect(parseCssColour("color(srgb 1 0.5 0)")).toEqual([255, 127.5, 0]);
    expect(parseCssColour("var(--x)")).toBeNull();
  });
});

describe("cost at size", () => {
  // Wall-clock budgets with wide headroom for a loaded CI box; the figures
  // logged are what the report quotes.
  for (const n of [2000, 8000]) {
    it(`lays out, indexes and batches ${n.toLocaleString()} files quickly`, () => {
      const files = synthetic(n, 40);
      const t0 = performance.now();
      const galaxies = layoutField(files);
      const t1 = performance.now();
      const scene = buildScene(galaxies);
      const grid = buildHitGrid(scene.nodes);
      const t2 = performance.now();
      const fills = scene.nodes.map((nd) => OVERLAY_SPECS.health.fill(nd.file));
      const batches = groupBatches(scene, fills, null, -1, () => true, () => [0, 0, 0]);
      const t3 = performance.now();
      let hits = 0;
      for (let k = 0; k < 10000; k++) {
        if (hitTest(grid, (k * 7.3) % WORLD, (k * 3.1) % WORLD, 2) >= 0) hits++;
      }
      const t4 = performance.now();
      console.info(
        `[map bench] n=${n} layout=${(t1 - t0).toFixed(1)}ms grid=${(t2 - t1).toFixed(1)}ms ` +
          `batches=${(t3 - t2).toFixed(1)}ms (${batches.length}) 10k hit-tests=${(t4 - t3).toFixed(1)}ms`,
      );
      expect(scene.nodes).toHaveLength(n);
      expect(hits).toBeGreaterThan(0);
      expect(t1 - t0).toBeLessThan(1500);
      expect(t4 - t3).toBeLessThan(500);
    });
  }
});
