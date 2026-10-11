import { describe, it, expect } from "vitest";
import {
  CO_CHANGES,
  describeCap,
  describeRelations,
  toggleVerb,
  indexRelationsByNode,
  summarizeRelations,
} from "../../src/zoom/relation-summary";
import { EDGE_MAX_PER_PARENT } from "../../src/zoom/constants";
import type { ZoomMap, ZoomRelation } from "../../src/zoom/types";

function rel(source: string, target: string, label: string, count = 1): ZoomRelation {
  return {
    parent_id: "p",
    source_id: source,
    target_id: target,
    label,
    edge_count: count,
    coupling: "loose",
  };
}

function map(relations: ZoomRelation[]): ZoomMap {
  return {
    root_id: "p",
    project_name: "t",
    total_files: 0,
    max_depth: 1,
    truncated: false,
    nodes: [],
    relations,
  };
}

describe("indexRelationsByNode", () => {
  it("indexes a relation under both endpoints", () => {
    const byNode = indexRelationsByNode(map([rel("a", "b", "imports")]));
    expect(byNode.get("a")).toHaveLength(1);
    expect(byNode.get("b")).toHaveLength(1);
  });

  it("drops self-loops, which the canvas never draws either", () => {
    const byNode = indexRelationsByNode(map([rel("a", "a", "imports")]));
    expect(byNode.get("a")).toBeUndefined();
  });
});

describe("summarizeRelations", () => {
  it("orders verbs by count so the dominant one reads first", () => {
    const s = summarizeRelations([
      rel("a", "b", "imports"),
      rel("a", "c", CO_CHANGES),
      rel("a", "d", "imports"),
    ]);
    expect(s.total).toBe(3);
    expect(s.verbs.map((v) => v.verb)).toEqual(["imports", CO_CHANGES]);
    expect(s.verbs[0]!.count).toBe(2);
    expect(s.coChanges).toBe(1);
  });

  it("reports `shown` as the per-parent cap once the total exceeds it", () => {
    const many = Array.from({ length: EDGE_MAX_PER_PARENT + 5 }, (_, i) =>
      rel("a", `b${i}`, "imports"),
    );
    expect(summarizeRelations(many).shown).toBe(EDGE_MAX_PER_PARENT);
    expect(summarizeRelations([rel("a", "b", "imports")]).shown).toBe(1);
  });
});

describe("describeRelations", () => {
  it("collapses the single-verb case, which is 89% of boxes on a real index", () => {
    const s = summarizeRelations([
      rel("a", "b", "imports"),
      rel("a", "c", "imports"),
      rel("a", "d", "imports"),
    ]);
    expect(describeRelations(s)).toBe("3 relations, all imports");
  });

  it("enumerates only when the verbs actually differ", () => {
    const s = summarizeRelations([
      rel("a", "b", "imports"),
      rel("a", "c", "imports"),
      rel("a", "d", CO_CHANGES),
    ]);
    expect(describeRelations(s)).toBe("3 relations: 2 imports, 1 co-changes");
  });

  it("keeps the singular readable rather than saying '1 relations, all imports'", () => {
    expect(describeRelations(summarizeRelations([rel("a", "b", "imports")]))).toBe(
      "1 imports relation",
    );
  });

  it("says so when there is nothing to show", () => {
    expect(describeRelations(summarizeRelations([]))).toBe("No relations at this level");
  });
});

describe("describeCap", () => {
  it("is silent when the view is complete", () => {
    expect(describeCap(summarizeRelations([rel("a", "b", "imports")]))).toBeNull();
  });

  it("names the bound when the cap hides relations, since a silent cap is a lie", () => {
    const many = Array.from({ length: EDGE_MAX_PER_PARENT + 1 }, (_, i) =>
      rel("a", `b${i}`, "imports"),
    );
    expect(describeCap(summarizeRelations(many))).toBe(
      `Showing the ${EDGE_MAX_PER_PARENT} strongest`,
    );
  });
});

describe("toggleVerb", () => {
  it("starts a filter from the unfiltered state", () => {
    expect([...(toggleVerb(null, "calls") ?? [])]).toEqual(["calls"]);
  });

  it("accumulates, so the control is a multi-select and not a radio group", () => {
    const one = toggleVerb(null, "calls");
    expect([...(toggleVerb(one, "imports") ?? [])].sort()).toEqual(["calls", "imports"]);
  });

  it("returns to null when the last verb is removed, never to an empty set", () => {
    // An empty set would draw no arrows at all, which is not what clearing the
    // last chip asks for.
    expect(toggleVerb(new Set(["calls"]), "calls")).toBeNull();
  });

  it("does not mutate the set it was given", () => {
    const before = new Set(["calls"]);
    toggleVerb(before, "imports");
    expect([...before]).toEqual(["calls"]);
  });
});
