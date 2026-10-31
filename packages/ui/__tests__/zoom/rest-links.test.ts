import { describe, expect, it } from "vitest";
import type { Camera } from "../../src/zoom/camera";
import { EDGE_MAX_PER_PARENT } from "../../src/zoom/constants";
import { type DrawOptions, drawScene } from "../../src/zoom/draw-tree";
import {
  REST_EDGE_MAX,
  edgeStrokePx,
  isHiddenTestLink,
  isTestSubtree,
  restLinkSummary,
} from "../../src/zoom/rest-links";
import { buildScene } from "../../src/zoom/scene";
import type { ZoomPalette } from "../../src/zoom/theme";
import type { ZoomMap, ZoomNode, ZoomRelation } from "../../src/zoom/types";
import { countingContext } from "./synthetic-map";

const palette = new Proxy({}, { get: () => "black" }) as ZoomPalette;
const VP = { w: 1600, h: 1000 };

function node(id: string, parent: string | null, kind: ZoomNode["kind"], isTest = false): ZoomNode {
  return {
    id,
    parent_id: parent,
    level: 0,
    kind,
    name: id,
    path: id,
    children: [],
    importance: 0.5,
    sibling_rank: 1,
    metrics: {
      file_count: 1,
      descendant_count: 0,
      hotspot_count: 0,
      dead_count: 0,
      entry_point_count: 0,
      on_flow_count: 0,
    },
    summary: "",
    language: null,
    page_id: "",
    health_score: null,
    is_entry_point: false,
    is_hotspot: false,
    is_dead: false,
    is_test: isTest,
    on_flow: false,
  };
}

function rel(parent: string, a: string, b: string, count: number, label = "calls"): ZoomRelation {
  return { parent_id: parent, source_id: a, target_id: b, label, edge_count: count, coupling: "loose" };
}

/**
 * Layers `l0..l{n-1}` of two files each, the last one (`tests`) made of test
 * files, joined by `rels`.
 */
function levelMap(layers: number, rels: (ids: string[]) => ZoomRelation[]): ZoomMap {
  const root = node("root", null, "system");
  const nodes: ZoomNode[] = [root];
  const ids: string[] = [];
  for (let l = 0; l < layers; l++) {
    const id = l === layers - 1 ? "tests" : `l${l}`;
    const layer = node(id, "root", "layer");
    root.children.push(id);
    ids.push(id);
    nodes.push(layer);
    for (let f = 0; f < 2; f++) {
      const file = node(`${id}/${f}`, id, "file", id === "tests");
      layer.children.push(file.id);
      nodes.push(file);
    }
  }
  return {
    root_id: "root",
    project_name: "t",
    total_files: layers * 2,
    max_depth: 2,
    truncated: false,
    nodes,
    relations: rels(ids),
  };
}

/** Four layers: three production ones and `tests`, whose links are the strongest. */
const small = levelMap(4, ([a, b, c, t]) => [
  rel("root", t!, a!, 5000),
  rel("root", t!, b!, 4000),
  rel("root", a!, b!, 900),
  rel("root", b!, c!, 120),
  rel("tests", "tests/0", "tests/1", 3),
]);

function arrows(map: ZoomMap, opts: Partial<DrawOptions> = {}): number {
  const scene = buildScene(map);
  const r = scene.worldRects.get(map.root_id)!;
  const cam: Camera = { cx: r.x + r.w / 2, cy: r.y + r.h / 2, scale: (VP.w * 0.9) / r.w };
  const { ctx, counts } = countingContext();
  drawScene(ctx, scene, cam, VP, palette, { selectedId: null, hoveredId: null, lowDetail: false, ...opts });
  return counts.get("bezierCurveTo") ?? 0;
}

describe("edgeStrokePx", () => {
  it("steps the stroke at 300 and 1,000 edges", () => {
    expect(edgeStrokePx(1)).toBeLessThan(edgeStrokePx(300));
    expect(edgeStrokePx(299)).toBe(edgeStrokePx(1));
    expect(edgeStrokePx(1000)).toBe(edgeStrokePx(300));
    expect(edgeStrokePx(1001)).toBeGreaterThan(edgeStrokePx(1000));
  });
});

describe("test links", () => {
  const nodes = new Map(small.nodes.map((n) => [n.id, n]));

  it("reads a layer of test files as tests, by what it holds", () => {
    expect(isTestSubtree(nodes, "tests")).toBe(true);
    expect(isTestSubtree(nodes, "l0")).toBe(false);
    expect(isTestSubtree(nodes, "tests/0")).toBe(true);
  });

  it("hides links touching tests, except inside the test subtree itself", () => {
    const [ta, , ab, , inside] = small.relations;
    expect(isHiddenTestLink(nodes, ta!)).toBe(true);
    expect(isHiddenTestLink(nodes, ab!)).toBe(false);
    expect(isHiddenTestLink(nodes, inside!)).toBe(false);
  });
});

describe("restLinkSummary", () => {
  it("counts the level's links and what the at-rest set may show", () => {
    expect(restLinkSummary(small, "root", { relationVerbs: null, showTestLinks: false })).toEqual({
      shown: 2,
      total: 4,
    });
    expect(restLinkSummary(small, "root", { relationVerbs: null, showTestLinks: true })).toEqual({
      shown: 4,
      total: 4,
    });
    expect(
      restLinkSummary(small, "root", { relationVerbs: new Set(["imports"]), showTestLinks: true }),
    ).toEqual({ shown: 0, total: 0 });
  });

  it("caps what it shows at REST_EDGE_MAX", () => {
    const many = levelMap(12, (ids) => ids.slice(1).map((id, i) => rel("root", ids[i]!, id, 10 + i)));
    expect(restLinkSummary(many, "root", { relationVerbs: null, showTestLinks: true })).toEqual({
      shown: REST_EDGE_MAX,
      total: 11,
    });
  });
});

describe("at-rest arrows", () => {
  it("draw nothing unless a level is given", () => {
    expect(arrows(small)).toBe(0);
  });

  it("draw the level's links with the test links left out", () => {
    expect(arrows(small, { levelId: "root" })).toBe(2);
  });

  it("include the test links when asked", () => {
    expect(arrows(small, { levelId: "root", showTestLinks: true })).toBe(4);
  });

  it("draw only the strongest REST_EDGE_MAX of a busy level", () => {
    const many = levelMap(12, (ids) => ids.slice(1).map((id, i) => rel("root", ids[i]!, id, 10 + i)));
    expect(arrows(many, { levelId: "root", showTestLinks: true })).toBe(REST_EDGE_MAX);
  });

  it("keep the rest of the level drawn under a hovered card's own links", () => {
    // l2's one link (b -> c) lifts; the other production link stays, dimmed.
    expect(arrows(small, { levelId: "root", hoveredId: "l2" })).toBe(2);
    // A hovered card's links include its test links: they are its own.
    expect(arrows(small, { levelId: "root", hoveredId: "tests" })).toBe(4);
  });

  it("draw nothing mid-pan", () => {
    expect(arrows(small, { levelId: "root", lowDetail: true })).toBe(0);
  });

  it("stay within both caps on a hovered busy level", () => {
    // l0 links to all 11 others (more than the focus cap); the rest chain.
    const many = levelMap(12, (ids) => [
      ...ids.slice(1).map((id) => rel("root", ids[0]!, id, 50)),
      ...ids.slice(2).map((id, i) => rel("root", ids[i + 1]!, id, 20)),
    ]);
    const n = arrows(many, { levelId: "root", hoveredId: "l0", showTestLinks: true });
    expect(n).toBe(EDGE_MAX_PER_PARENT + REST_EDGE_MAX);
  });
});
