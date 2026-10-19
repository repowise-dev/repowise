/**
 * The area map's geometry, semantic zoom and picking, as pure functions: it
 * must read for a five-file repository and for an eight-thousand-file one,
 * load lazily without moving anything already drawn, and stay cheap.
 */
import { describe, it, expect } from "vitest";
import type { AreaNode, AreaTree } from "@repowise-dev/types/areas";
import { WORLD, fitCamera } from "../../src/health/map/layout.js";
import { EMPTY_AREA_INDEX, deriveAreaIndex, mergeAreaTree } from "../../src/health/map/areas.js";
import {
  areaTrail,
  chainEnd,
  displayChildren,
  displayLabel,
  displayParent,
  hostOf,
} from "../../src/health/map/area-paths.js";
import { layoutAreas, layoutFiles, type Circle } from "../../src/health/map/area-layout.js";
import { OPEN_PX, computeView, discAt } from "../../src/health/map/area-view.js";
import { buildHitGrid, hitTest } from "../../src/health/map/hit-test.js";
import { buildScene, groupBatches, parseCssColour } from "../../src/health/map/paint.js";
import { OVERLAY_SPECS } from "../../src/health/map/lens-registry.js";
import type { CodeHealthMapFile } from "../../src/health/map/types.js";
import { mapFile as f } from "./map-test-utils.js";

/** A synthetic repository: `n` files over `modules` packages, sizes skewed. */
function synthetic(n: number, modules: number): CodeHealthMapFile[] {
  return Array.from({ length: n }, (_, i) => {
    const m = i % modules;
    return f(`pkg${m}/sub${i % 7}/deep${i % 3}/file${i}.py`, 5 + ((i * 7919) % 900), `pkg${m}`, {
      score: 1 + ((i * 31) % 90) / 10,
    });
  });
}

const inside = (inner: Circle, outer: Circle, eps = 1e-6) =>
  Math.hypot(inner.x - outer.x, inner.y - outer.y) + inner.r <= outer.r + eps;

/** A server area node with the fields the map reads. */
function node(path: string, parent: string | null, file_count: number, nloc: number, extra: Partial<AreaNode> = {}): AreaNode {
  return {
    path,
    name: path.split("/").pop() ?? "",
    depth: path ? path.split("/").length : 0,
    parent,
    has_children: false,
    direct_file_count: 0,
    file_count,
    nloc,
    health: 7,
    band: "good",
    band_counts: { at_risk: 0, needs_work: 0, fair: 0, good: file_count, excellent: 0 },
    worst_file: { path: null, score: 7 },
    hotspot_count: 0,
    bus_factor_1_count: 0,
    primary_owner: null,
    line_coverage_pct: null,
    coverage_file_count: 0,
    findings: { critical: 0, high: 0, medium: 0, low: 0 },
    health_delta_30d: null,
    ...extra,
  };
}

function tree(root: AreaNode, depth: number, nodes: AreaNode[]): AreaTree {
  return { root, depth, nodes, delta_window: { target_days: 30, latest_at: null, baseline_at: null } };
}

describe("the area hierarchy", () => {
  const files = [
    f("README.md", 10, null),
    f("packages/core/src/lib/a.py", 300, "core"),
    f("packages/core/src/lib/b.py", 100, "core"),
    f("packages/core/src/api.py", 50, "core"),
    f("packages/ui/c.ts", 80, "ui"),
  ];
  const index = deriveAreaIndex(files);

  it("derives every directory with its rollup from a flat feed", () => {
    expect(index.areas.get("")!.fileCount).toBe(5);
    expect(index.areas.get("packages/core")!.nloc).toBe(450);
    expect(index.areas.get("packages/core/src")!.directFileCount).toBe(1);
    // NLOC-weighted, the server's fold: every file here scores 7.
    expect(index.areas.get("packages")!.health).toBe(7);
    expect(index.areas.get("packages")!.band).toBe("good");
  });

  it("counts an empty file in its folders, as the server does, without weighing it as code", () => {
    const withEmpty = deriveAreaIndex([
      f("lib/a.py", 100, null, { score: 9 }),
      f("lib/__init__.py", 0, null, { score: 1 }),
    ]);
    const lib = withEmpty.areas.get("lib")!;
    expect([lib.fileCount, lib.directFileCount, lib.nloc]).toEqual([2, 2, 100]);
    // Weighted with the floor of 1: (9 * 100 + 1 * 1) / 101.
    expect(lib.health).toBe(8.92);
  });

  it("collapses a directory chain into one disc named by the whole chain", () => {
    // packages/core holds only src: one disc, named "core/src".
    expect(chainEnd(index, "packages/core")).toBe("packages/core/src");
    expect(displayChildren(index, "packages")).toEqual(["packages/core/src", "packages/ui"]);
    expect(displayParent(index, "packages/core/src")).toBe("packages");
    expect(displayLabel(index, "packages/core/src", "Repo")).toBe("core/src");
    expect(areaTrail(index, "packages/core/src/lib")).toEqual([
      "",
      "packages",
      "packages/core/src",
      "packages/core/src/lib",
    ]);
  });

  it("hosts each file in its own directory's slot", () => {
    expect(hostOf(index, "packages/core/src/api.py")).toEqual({ area: "packages/core/src", direct: true });
    expect(hostOf(index, "README.md")).toEqual({ area: "", direct: true });
  });

  it("hosts a file under an area not unfolded yet in that area's whole disc", () => {
    const lazy = mergeAreaTree(
      EMPTY_AREA_INDEX,
      tree(node("", null, 3, 300, { has_children: true }), 1, [
        node("src", "", 3, 300, { has_children: true }),
      ]),
    );
    expect(lazy.expanded.has("")).toBe(true);
    expect(lazy.expanded.has("src")).toBe(false);
    expect(hostOf(lazy, "src/deep/x.py")).toEqual({ area: "src", direct: false });
  });
});

describe("nested layout", () => {
  const files = synthetic(1200, 6);
  const index = deriveAreaIndex(files);
  const geo = layoutAreas(index);

  it("is deterministic", () => {
    expect([...layoutAreas(deriveAreaIndex(files)).circles]).toEqual([...geo.circles]);
  });

  it("nests every disc inside its parent and keeps siblings apart", () => {
    for (const [path, c] of geo.circles) {
      const parent = displayParent(index, path);
      if (parent === null) continue;
      expect(inside(c, geo.circles.get(parent)!)).toBe(true);
      const sibs = displayChildren(index, parent).filter((s) => s !== path);
      for (const s of sibs) {
        const o = geo.circles.get(s)!;
        expect(Math.hypot(c.x - o.x, c.y - o.y)).toBeGreaterThanOrEqual(c.r + o.r - 1e-6);
      }
    }
  });

  it("unfolding a level fills a disc and moves nothing already drawn", () => {
    const top = tree(node("", null, 4, 400, { has_children: true }), 1, [
      node("a", "", 3, 300, { has_children: true }),
      node("b", "", 1, 100, { direct_file_count: 1 }),
    ]);
    const before = layoutAreas(mergeAreaTree(EMPTY_AREA_INDEX, top));
    const deeper = mergeAreaTree(
      mergeAreaTree(EMPTY_AREA_INDEX, top),
      tree(node("a", "", 3, 300, { has_children: true }), 1, [
        node("a/x", "a", 2, 200, { direct_file_count: 2 }),
        node("a/y", "a", 1, 100, { direct_file_count: 1 }),
      ]),
    );
    const after = layoutAreas(deeper);
    for (const [path, c] of before.circles) expect(after.circles.get(path)).toEqual(c);
    expect(inside(after.circles.get("a/x")!, after.circles.get("a")!)).toBe(true);
  });

  it("places every drawable file exactly once, inside its host, never overlapping", () => {
    const placed = layoutFiles(files, index, geo);
    expect(placed.unplaced).toBe(0);
    const paths = placed.nodes.map((n) => n.file.file_path);
    expect(new Set(paths).size).toBe(files.length);
    for (const h of placed.hosts) {
      const ns = [...h.nodes].map((i) => placed.nodes[i]!);
      for (const n of ns) expect(inside(n, h.circle, 1e-6)).toBe(true);
      for (let i = 0; i < Math.min(ns.length, 40); i++) {
        for (let j = i + 1; j < Math.min(ns.length, 40); j++) {
          const a = ns[i]!;
          const b = ns[j]!;
          expect(Math.hypot(a.x - b.x, a.y - b.y)).toBeGreaterThanOrEqual(a.r + b.r - 1e-6);
        }
      }
    }
  });
});

describe("semantic zoom", () => {
  const files = synthetic(3000, 8);
  const index = deriveAreaIndex(files);
  const geo = layoutAreas(index);
  const placed = layoutFiles(files, index, geo);
  const view = { w: 800, h: 640, dpr: 1 };
  const at = (cam: { cx: number; cy: number; scale: number }) =>
    computeView({ index, geo, hosts: placed.hosts, hostIndex: placed.hostIndex, filesCovered: () => true, cam, view });

  it("zoomed out, loaded files are texture inside faint folder containers", () => {
    const v = at(fitCamera(view.w, view.h));
    expect(v.drawnHosts.some((x) => x === 1)).toBe(true);
    expect(v.discs.find((d) => d.path === "pkg0")!.state).toBe("open");
  });

  it("once a dot would be under a device pixel, folders are solid and draw no dots", () => {
    const far = fitCamera(view.w, view.h);
    const v = at({ ...far, scale: far.scale / 15 });
    expect(v.drawnHosts.every((x) => x === 0)).toBe(true);
    expect(v.discs.some((d) => d.state === "closed")).toBe(true);
  });

  it("zoomed into one leaf area, draws its files", () => {
    const leaf = "pkg0/sub0/deep0";
    const c = geo.circles.get(leaf)!;
    const v = at({ cx: c.x, cy: c.y, scale: (view.h * 0.9) / (2 * c.r) });
    const host = placed.hosts.findIndex((h) => h.area === leaf);
    expect(v.drawnHosts[host]).toBe(1);
    expect(v.discs.find((d) => d.path === leaf)!.state).toBe("open");
    expect(discAt(v, view.w / 2, view.h / 2)!.path).toBe(leaf);
  });

  it("asks for children when an area opens, and files from the shallowest area that fits", () => {
    const lazy = mergeAreaTree(
      EMPTY_AREA_INDEX,
      tree(node("", null, 9000, 40000, { has_children: true }), 1, [
        node("big", "", 300, 30000, { has_children: true }),
        node("tiny", "", 8700, 10000, { has_children: true }),
      ]),
    );
    const lgeo = layoutAreas(lazy);
    const big = lgeo.circles.get("big")!;
    const v = computeView({
      index: lazy,
      geo: lgeo,
      hosts: [],
      hostIndex: new Map(),
      filesCovered: () => false,
      cam: { cx: big.x, cy: big.y, scale: (OPEN_PX * 4) / big.r },
      view,
    });
    expect(v.wantChildren).toContain("big");
    expect(v.wantFiles).toEqual(["big"]);
  });
});

describe("hit testing", () => {
  const files = synthetic(3000, 12);
  const index = deriveAreaIndex(files);
  const nodes = layoutFiles(files, index, layoutAreas(index)).nodes;
  const grid = buildHitGrid(nodes);

  it("finds the node under its own centre", () => {
    for (const i of [0, 17, 999, 2999]) {
      const n = nodes[i]!;
      expect(hitTest(grid, n.x, n.y)).toBe(i);
    }
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
      const index = deriveAreaIndex(files);
      const geo = layoutAreas(index);
      const placed = layoutFiles(files, index, geo);
      const t1 = performance.now();
      const scene = buildScene(placed);
      const grid = buildHitGrid(scene.nodes);
      const fills = scene.nodes.map((nd) => OVERLAY_SPECS.health.fill(nd.file));
      const batches = groupBatches(scene, fills, null, () => true, () => [0, 0, 0]);
      const t2 = performance.now();
      const cam = fitCamera(800, 640);
      const view = { w: 800, h: 640, dpr: 1 };
      for (let k = 0; k < 100; k++) {
        computeView({ index, geo, hosts: scene.hosts, hostIndex: scene.hostIndex, filesCovered: () => true, cam: { ...cam, scale: cam.scale * (1 + k / 10) }, view });
      }
      const t3 = performance.now();
      console.info(
        `[map bench] n=${n} areas=${index.areas.size} layout=${(t1 - t0).toFixed(1)}ms ` +
          `scene+batches=${(t2 - t1).toFixed(1)}ms (${batches.length}) view/frame=${((t3 - t2) / 100).toFixed(2)}ms`,
      );
      expect(scene.nodes).toHaveLength(n);
      expect(hitTest(grid, scene.nodes[0]!.x, scene.nodes[0]!.y)).toBe(0);
      expect(t1 - t0).toBeLessThan(3000);
      expect((t3 - t2) / 100).toBeLessThan(8);
    });
  }
});
