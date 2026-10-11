import { describe, expect, it } from "vitest";
import type { RefactoringPlan } from "../src/refactoring";

const legacyServerPlan: RefactoringPlan = {
  id: "legacy",
  refactoring_type: "extract_method",
  file_path: "src/a.py",
  target_symbol: "run",
  line_start: 1,
  line_end: 20,
  plan: {},
  evidence: {},
  impact_delta: 1,
  effort_bucket: "M",
  blast_radius: {},
  confidence: "medium",
  source_biomarker: "complex_method",
  rank_score: 1.25,
};

describe("refactoring recommendation contract", () => {
  it("accepts an older server payload with the newer fields missing", () => {
    expect(legacyServerPlan.benefit).toBeUndefined();
    expect(legacyServerPlan.validation).toBeUndefined();
  });

  it("types the canonical validation and priority components", () => {
    const current: RefactoringPlan = {
      ...legacyServerPlan,
      benefit: 2,
      leverage: 1.5,
      cost: 2.25,
      risk: 0.75,
      validation: {
        basis: "measured",
        via: "coverage",
        total: 1,
        tests: ["tests/test_a.py::test_run"],
        truncated: false,
        affected_files: ["src/a.py"],
        affected_symbols: ["run"],
        commands: ["pytest tests/test_a.py::test_run"],
        targets: [
          {
            file_path: "src/a.py",
            basis: "measured",
            via: "coverage",
            total: 1,
            tests: ["tests/test_a.py::test_run"],
            truncated: false,
          },
        ],
      },
    };
    expect(current.validation?.basis).toBe("measured");
    expect(current.risk).toBe(0.75);
  });
});
