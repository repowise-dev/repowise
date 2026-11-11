import { describe, expect, it } from "vitest";

import {
  actionabilityCounts,
  areaGroups,
  countsFromFacet,
  excludedReviews,
  facetAreaGroups,
} from "../../src/health/performance/grouping";
import { scopeAccount } from "../../src/health/performance/scope";
import { verdictOf } from "../../src/health/performance/verdict";
import { opportunity } from "./fixtures/performance";

const at = (id: string, file_path: string, state: "plan_ready" | "advisory" | "investigate", rank: number) =>
  opportunity({ opportunity_id: id, file_path, actionability_state: state, rank_position: rank });

const items = [
  at("a", "pkg/core/a.py", "advisory", 0),
  at("b", "pkg/server/b.py", "plan_ready", 1),
  at("c", "pkg/core/c.py", "advisory", 2),
  at("d", "pkg/web/d.py", "investigate", 3),
];

describe("performance grouping", () => {
  it("counts the loaded result by actionability", () => {
    expect(actionabilityCounts(items)).toEqual({ plan_ready: 1, advisory: 2, investigate: 1, expected: 0 });
  });

  it("reads the server's actionability facet as counts", () => {
    expect(countsFromFacet([{ value: "advisory", total: 4 }, { value: "bogus", total: 1 }])).toEqual({
      plan_ready: 0,
      advisory: 4,
      investigate: 0,
      expected: 0,
    });
    expect(countsFromFacet(undefined).advisory).toBe(0);
  });

  it("ranks areas by plan-ready, then advisory, keeping the server order inside", () => {
    const groups = areaGroups(items);
    expect(groups.map((g) => g.area)).toEqual(["pkg/server", "pkg/core", "pkg/web"]);
    expect(groups[1]!.items.map((i) => i.opportunity_id)).toEqual(["a", "c"]);
  });

  it("draws every area the server counts, holding the loaded causes at its depth", () => {
    const groups = facetAreaGroups(items, {
      depth: 2,
      total: 40,
      more: 0,
      items: [
        { area: "pkg/server", total: 10, actionability: { plan_ready: 1, expected: 9 } },
        { area: "pkg/core", total: 25, actionability: { advisory: 25 } },
        { area: "pkg/lib", total: 5, actionability: { investigate: 5 } },
      ],
    });
    expect(groups.map((g) => [g.area, g.total, g.items.length])).toEqual([
      ["pkg/server", 10, 1],
      ["pkg/core", 25, 2],
      // Counted by the server though no loaded cause sits in it.
      ["pkg/lib", 5, 0],
    ]);
    expect(groups[0]!.counts).toEqual({ plan_ready: 1, advisory: 0, investigate: 0, expected: 9 });
  });

  it("turns left-out counts into review links with the scope that lists them", () => {
    const reviews = excludedReviews({ test: 5, unmeasured_cost: 9, cold_path: 0, no_strategy: 2 });
    expect(reviews.map((r) => [r.reason, r.count])).toEqual([
      ["unmeasured_cost", 9],
      ["test", 5],
      ["no_strategy", 2],
    ]);
    expect(reviews[0]!.patch).toEqual({ scope: "production", proof: "unproven" });
    expect(reviews[1]!.patch).toEqual({ scope: "all", context: "test" });
    expect(reviews[2]!.patch).toEqual({ scope: "production", actionability: "investigate" });
    expect(excludedReviews(undefined)).toEqual([]);
  });

  it("leads with the scope's own count, named for the scope", () => {
    const groups = areaGroups(items);
    expect(verdictOf("now", 4, groups)).toEqual({
      value: 4,
      unit: "changes worth making now",
      areas: "Across 3 areas, led by pkg/server.",
    });
    expect(verdictOf("production", 1, groups.slice(0, 1)).unit).toBe("production cause");
    expect(verdictOf("all", 938, []).areas).toBeNull();
  });
});

describe("scope accounting", () => {
  const counts = { now: 4, production: 430, all: 938 };

  it("accounts for every hidden cause from the default queue", () => {
    expect(scopeAccount("now", counts)).toEqual([
      { count: 4, text: "worth doing now", scope: null },
      { count: 426, text: "more production causes are not ready to act on", scope: "production" },
      { count: 508, text: "are outside production code", scope: "all" },
    ]);
  });

  it("names the scope first and links the others", () => {
    expect(scopeAccount("production", counts).map((c) => [c.count, c.scope])).toEqual([
      [430, null],
      [4, "now"],
      [508, "all"],
    ]);
    expect(scopeAccount("all", counts).map((c) => [c.count, c.scope])).toEqual([
      [938, null],
      [430, "production"],
      [4, "now"],
    ]);
  });

  it("drops a clause with nothing behind it, but never the scope's own", () => {
    expect(scopeAccount("now", { now: 0, production: 0, all: 3 })).toEqual([
      { count: 0, text: "worth doing now", scope: null },
      { count: 3, text: "are outside production code", scope: "all" },
    ]);
  });
});
