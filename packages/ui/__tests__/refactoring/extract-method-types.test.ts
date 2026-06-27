import { describe, it, expect } from "vitest";
import {
  extractMethodPlan,
  generatedVerdict,
  planSynopsis,
  planWins,
  type GeneratedCode,
  type RefactoringPlan,
} from "../../src/refactoring/types";
import { typeMeta, TYPE_ORDER } from "../../src/refactoring/meta";

function extractMethodPlanFixture(overrides: Partial<RefactoringPlan> = {}): RefactoringPlan {
  return {
    id: "p1",
    refactoring_type: "extract_method",
    file_path: "pkg/pipeline.py",
    target_symbol: "run_pipeline",
    line_start: 10,
    line_end: 80,
    plan: {
      span: { start: 30, end: 48 },
      params: ["records", "threshold"],
      returns: ["average"],
      suggested_name: null,
    },
    evidence: { slice_nloc: 19, ccn_removed: 4 },
    impact_delta: 1.5,
    effort_bucket: "M",
    blast_radius: { callers_count: 0 },
    confidence: "high",
    source_biomarker: "complex_method",
    rank_score: 2.1,
    ...overrides,
  };
}

describe("extract_method plan accessors", () => {
  it("reads the span, params, and returns defensively", () => {
    const em = extractMethodPlan(extractMethodPlanFixture());
    expect(em.span).toEqual({ start: 30, end: 48 });
    expect(em.params).toEqual(["records", "threshold"]);
    expect(em.returns).toEqual(["average"]);
    expect(em.suggested_name).toBeNull();
  });

  it("returns a null span when the plan omits it", () => {
    const em = extractMethodPlan(extractMethodPlanFixture({ plan: { params: [], returns: [] } }));
    expect(em.span).toBeNull();
    expect(em.params).toEqual([]);
  });

  it("synopsis describes the lifted line count", () => {
    expect(planSynopsis(extractMethodPlanFixture())).toBe("Extract 19 lines into a helper");
  });

  it("wins lead with the recovered health and the complexity removed", () => {
    const wins = planWins(extractMethodPlanFixture());
    expect(wins[0]).toEqual({ hero: true, label: "+1.5 health recovered" });
    expect(wins.some((w) => w.label.includes("lifted into a focused helper"))).toBe(true);
    expect(wins.some((w) => w.label.includes("cyclomatic complexity"))).toBe(true);
  });
});

describe("extract_method meta", () => {
  it("has a label and is in the type order", () => {
    expect(typeMeta("extract_method").label).toBe("Extract Method");
    expect(TYPE_ORDER).toContain("extract_method");
  });
});

describe("extract_method generated verdict", () => {
  function generated(validation: Record<string, unknown>): GeneratedCode {
    return {
      suggestion_id: "s1",
      refactoring_type: "extract_method",
      file_path: "pkg/pipeline.py",
      target_symbol: "run_pipeline",
      content: "",
      diff: "",
      provider: "mock",
      model: "mock",
      cached: false,
      input_tokens: 0,
      output_tokens: 0,
      validation,
      spans: [],
    };
  }

  it("passes when the residual CCN dropped", () => {
    const v = generatedVerdict(
      generated({ status: "checked", original_ccn: 12, residual_ccn: 4, function_count: 2, improved: true }),
    );
    expect(v?.tone).toBe("pass");
    expect(v?.label).toBe("Complexity reduced");
    expect(v?.detail).toContain("CCN 12 → 4");
  });

  it("fails when complexity did not drop", () => {
    const v = generatedVerdict(
      generated({ status: "checked", original_ccn: 12, residual_ccn: 12, function_count: 1, improved: false }),
    );
    expect(v?.tone).toBe("fail");
  });

  it("is neutral when the self-check was skipped", () => {
    const v = generatedVerdict(generated({ status: "skipped", reason: "no walker" }));
    expect(v?.tone).toBe("neutral");
  });
});
