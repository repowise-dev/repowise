import { describe, expect, it } from "vitest";

import { PREVIEW_MAX_ITEMS, cardPreview, composition } from "../../src/zoom/card-preview";
import type { ZoomKind, ZoomNode } from "../../src/zoom/types";

function node(
  id: string,
  kind: ZoomKind,
  children: string[] = [],
  over: Partial<ZoomNode> = {},
): ZoomNode {
  return {
    id,
    parent_id: null,
    level: 0,
    kind,
    name: id,
    path: id,
    children,
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
    is_test: false,
    on_flow: false,
    ...over,
  };
}

function index(...nodes: ZoomNode[]): Map<string, ZoomNode> {
  return new Map(nodes.map((n) => [n.id, n]));
}

describe("cardPreview", () => {
  it("lists children by sibling rank with their band and file count", () => {
    const g = node("g", "group", ["b", "a"]);
    const a = node("a", "folder", ["a1"], {
      sibling_rank: 1,
      health_score: 9,
      metrics: { ...g.metrics, file_count: 7 },
    });
    const b = node("b", "file", [], { sibling_rank: 2, health_score: 2 });
    const p = cardPreview(index(g, a, b, node("a1", "file")), g)!;
    expect(p.items.map((i) => i.name)).toEqual(["a", "b"]);
    expect(p.items[0]).toMatchObject({ count: "7", band: "excellent" });
    expect(p.items[1]).toMatchObject({ count: "", band: "at_risk", mode: "path" });
    expect(p.total).toBe(2);
  });

  it("lists through a chain of lone children", () => {
    const g = node("g", "group", ["f"]);
    const f = node("f", "folder", ["x", "y"]);
    const p = cardPreview(index(g, f, node("x", "file"), node("y", "file")), g)!;
    expect(p.items.map((i) => i.name)).toEqual(["x", "y"]);
  });

  it("caps the items but counts every child", () => {
    const ids = Array.from({ length: PREVIEW_MAX_ITEMS + 5 }, (_, i) => `c${i}`);
    const g = node("g", "group", ids);
    const p = cardPreview(index(g, ...ids.map((id, i) => node(id, "file", [], { sibling_rank: i }))), g)!;
    expect(p.items).toHaveLength(PREVIEW_MAX_ITEMS);
    expect(p.total).toBe(PREVIEW_MAX_ITEMS + 5);
  });

  it("is null for a file or an unexpanded container, and cached", () => {
    const f = node("f", "file");
    const lone = node("l", "folder", ["missing"]);
    const nodes = index(f, lone);
    expect(cardPreview(nodes, f)).toBeNull();
    expect(cardPreview(nodes, lone)).toBeNull();
    const g = node("g", "group", ["f"]);
    const withKid = index(g, f);
    expect(cardPreview(withKid, g)).toBe(cardPreview(withKid, g));
  });
});

describe("composition", () => {
  it("counts files per band over the subtree, unexpanded children by their own band", () => {
    const root = node("r", "layer", ["ok", "bad", "folded", "unscored"]);
    const nodes = index(
      root,
      node("ok", "file", [], { health_score: 9.5 }), // excellent folds into good
      node("bad", "file", [], { health_score: 2 }),
      node("folded", "folder", ["not-served"], {
        health_score: 5.5,
        metrics: { ...root.metrics, file_count: 4 },
      }),
      node("unscored", "file"),
    );
    // at_risk, needs_work, fair, good, unscored
    const bands = composition(nodes, root);
    expect(bands.reduce((s, v) => s + v, 0)).toBe(7);
    expect(bands[0]).toBe(1);
    expect(bands[3]).toBe(1);
    expect(bands[4]).toBe(1);
    expect(bands[1]! + bands[2]!).toBe(4);
  });
});
