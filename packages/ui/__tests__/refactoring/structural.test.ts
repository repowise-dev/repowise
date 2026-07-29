import { describe, expect, it } from "vitest";

import {
  MAP_MIN_POINTS,
  isStructural,
  planPoint,
  planReason,
  STRUCTURAL_TYPES,
  type RefactoringPlan,
} from "../../src/refactoring/types";

function plan(over: Partial<RefactoringPlan> = {}): RefactoringPlan {
  return {
    id: "p1",
    refactoring_type: "split_file",
    file_path: "pkg/models.py",
    target_symbol: "models.py -> 6 files",
    line_start: null,
    line_end: null,
    plan: {},
    evidence: {},
    impact_delta: 0,
    effort_bucket: "XL",
    blast_radius: {},
    confidence: "high",
    source_biomarker: "",
    rank_score: 1,
    ...over,
  };
}

describe("isStructural", () => {
  it("separates the shape-changing types from the local ones", () => {
    for (const type of STRUCTURAL_TYPES) {
      expect(isStructural(plan({ refactoring_type: type }))).toBe(true);
    }
    expect(isStructural(plan({ refactoring_type: "extract_helper" }))).toBe(false);
    expect(isStructural(plan({ refactoring_type: "extract_method" }))).toBe(false);
  });

  it("treats an unknown type as local rather than throwing", () => {
    expect(isStructural(plan({ refactoring_type: "invent_monad" }))).toBe(false);
  });
});

describe("planPoint", () => {
  it("returns coordinates when both figures are measured", () => {
    expect(planPoint(plan({ dependents: 195, file_nloc: 1052 }))).toEqual({ x: 195, y: 1052 });
  });

  it("drops a plan whose figures are missing, rather than plotting it at the origin", () => {
    // An older backend omits both fields entirely.
    expect(planPoint(plan())).toBeNull();
    // A repo with graph metrics but no health pass has one and not the other.
    expect(planPoint(plan({ dependents: 12 }))).toBeNull();
    expect(planPoint(plan({ file_nloc: 400 }))).toBeNull();
  });

  it("treats a genuine zero as unmeasured", () => {
    // 0 dependents is what an unindexed file and an unimported file both report,
    // and the map cannot tell them apart. A false cluster on the axis would be
    // worse than an honest omission the key row counts.
    expect(planPoint(plan({ dependents: 0, file_nloc: 900 }))).toBeNull();
  });
});

describe("planReason", () => {
  it("reads split-file evidence into one sentence", () => {
    const reason = planReason(
      plan({
        evidence: {
          file_nloc: 1052,
          symbol_count: 39,
          group_count: 6,
          intra_edges: 45,
          cut_edges: 6,
          cochange_edges: 51,
        },
      }),
    );
    expect(reason).toContain("1,052 lines and 39 symbols");
    expect(reason).toContain("6 of 45 internal edges cross a seam");
    expect(reason).toContain("51 pairs");
  });

  it("says so when a split has a clean seam", () => {
    const reason = planReason(
      plan({ evidence: { file_nloc: 426, symbol_count: 22, group_count: 4, intra_edges: 15, cut_edges: 0 } }),
    );
    expect(reason).toContain("No edges cross a seam");
  });

  it("counts a cycle and its cuts", () => {
    const reason = planReason(
      plan({
        refactoring_type: "break_cycle",
        evidence: { cycle_size: 7, edge_count: 9, cut_count: 3 },
      }),
    );
    expect(reason).toBe("7 modules import each other in a ring across 9 edges. 3 cuts open it.");
  });

  it("keeps the singular readable", () => {
    const reason = planReason(
      plan({
        refactoring_type: "break_cycle",
        evidence: { cycle_size: 2, edge_count: 2, cut_count: 1 },
      }),
    );
    expect(reason).toContain("1 cut opens it");
  });

  it("returns an empty string when there is no evidence, so a caller renders nothing", () => {
    expect(planReason(plan({ evidence: {} }))).toBe("");
    expect(planReason(plan({ refactoring_type: "break_cycle", evidence: {} }))).toBe("");
    expect(planReason(plan({ refactoring_type: "invent_monad" }))).toBe("");
  });
});

describe("MAP_MIN_POINTS", () => {
  it("is high enough that a scatter is never drawn over a handful of dots", () => {
    // The floor exists so a repo with two structural plans gets rows instead of
    // two marks under two axis labels. Guarded because lowering it is the kind
    // of change that looks harmless in a diff.
    expect(MAP_MIN_POINTS).toBeGreaterThanOrEqual(5);
  });
});
