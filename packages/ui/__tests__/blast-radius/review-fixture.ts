import type { ChangeReview } from "@repowise-dev/types/blast-radius";

/** A scored change with two selected tests; override any part. */
export function makeReview(over: { risk?: Partial<ChangeReview["risk"]>; tests?: Partial<ChangeReview["tests"]>; commands?: string[] } = {}): ChangeReview {
  return {
    revspec: "abc",
    risk: {
      ref: "abc",
      working_tree: false,
      fix_history: { available: true, density: 0.1, percentile: 40, files: [] },
      risk_authority: {
        authoritative_for: "review_priority",
        primary_fields: ["fix_history"],
        primary_basis: "",
        fallback_field: "fallback_band",
        fallback_basis: "",
        score_role: "",
      },
      score: 6.25,
      score_measures: "diff shape",
      score_unit: "points",
      risk_percentile: 82.4,
      review_priority: "high",
      classification: "Elevated",
      fallback_band: null,
      baseline_sample_size: 120,
      exclude_patterns: [],
      is_fix: false,
      features: { la: 400, ld: 10, nf: 6, nd: 3, ns: 2, entropy: 1.5, exp: 12 },
      drivers: [
        { feature: "la", value: 400, contribution: 1.2, label: "more lines" },
        { feature: "exp", value: 12, contribution: -0.4, label: "experienced" },
        { feature: "nf", value: 6, contribution: 0.5, label: "more files" },
      ],
      ...over.risk,
    },
    tests: {
      status: "selected",
      run_all: false,
      reasons: [],
      basis: "inferred",
      tests_to_run: ["tests/test_a.py", "tests/test_b.py"],
      total: 2,
      truncated: false,
      summary: "2 tests reach the change.",
      ...over.tests,
    },
    test_commands: over.commands ?? ["pytest tests/test_a.py tests/test_b.py"],
  };
}
