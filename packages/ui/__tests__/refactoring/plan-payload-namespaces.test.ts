/**
 * The published surfaces for two plan fields that used to be constants.
 *
 * Fixing them in the backend alone would be half a fix: `suggested_name` was
 * `null` on every extract_method plan, so the prompt builder and the plan
 * detail always fell through to a generic "helper" — the user-facing
 * consequence lived here, not in Python. And `suggested_site` carried a graph
 * community label under `module` alongside a filesystem `directory`, which
 * `helperSite` preferred; plans stored before that removal still carry it.
 */
import { describe, it, expect } from "vitest";
import { helperSite, extractMethodPlan, type RefactoringPlan } from "../../src/refactoring/types";
import { buildRefactoringPlanPrompt } from "../../src/health/ai-prompt-builder";

function helperPlan(suggested_site: Record<string, unknown>): RefactoringPlan {
  return {
    id: "p1",
    refactoring_type: "extract_helper",
    file_path: "pkg/api/a.py",
    target_symbol: "a.py:10-25",
    line_start: 10,
    line_end: 25,
    plan: {
      occurrences: [
        { file: "pkg/api/a.py", line_start: 10, line_end: 25 },
        { file: "pkg/core/b.py", line_start: 40, line_end: 55 },
      ],
      suggested_site,
      duplicated_lines: 16,
      suggested_name: "pkg_helper",
    },
    evidence: { duplicated_lines: 16, occurrence_count: 2 },
    impact_delta: 0.9,
    effort_bucket: "S",
    blast_radius: { files: ["pkg/core/b.py"], file_count: 1 },
    confidence: "high",
    source_biomarker: "dry_violation",
    rank_score: 1.4,
  };
}

function methodPlan(suggested_name: string | null): RefactoringPlan {
  return {
    id: "p2",
    refactoring_type: "extract_method",
    file_path: "pkg/pipeline.py",
    target_symbol: "run_pipeline",
    line_start: 10,
    line_end: 80,
    plan: {
      span: { start: 30, end: 48 },
      params: ["records", "threshold"],
      returns: ["average"],
      suggested_name,
    },
    evidence: { slice_nloc: 19, ccn_removed: 4 },
    impact_delta: 1.5,
    effort_bucket: "M",
    blast_radius: { scope: "local" },
    confidence: "high",
    source_biomarker: "complex_method",
    rank_score: 2.1,
  };
}

describe("helperSite namespace", () => {
  it("reads the directory, the only namespace a plan now carries", () => {
    expect(helperSite(helperPlan({ directory: "pkg" }))).toBe("pkg");
  });

  it("prefers the directory over a legacy community label on a stored plan", () => {
    // The measured shape: on 905 of 905 labelled rows the label named a
    // directory no occurrence lived in, while `directory` was correct. Reading
    // the label first is what put `ui` on a block spanning three packages.
    expect(helperSite(helperPlan({ module: "ui", directory: "pkg" }))).toBe("pkg");
  });

  it("salvages the label only when a legacy row has no directory at all", () => {
    expect(helperSite(helperPlan({ module: "ui", directory: null }))).toBe("ui");
  });

  it("returns null when the plan carries no site", () => {
    expect(helperSite(helperPlan({}))).toBeNull();
  });
});

describe("extract_method suggested_name reaches the rendered prompt", () => {
  it("uses the computed name instead of the generic fallback", () => {
    const prompt = buildRefactoringPlanPrompt({ plan: methodPlan("compute_average") });
    expect(prompt).toContain("compute_average");
    expect(prompt).not.toContain("a clearly named helper");
  });

  it("keeps the generic fallback for a plan stored before names were computed", () => {
    const prompt = buildRefactoringPlanPrompt({ plan: methodPlan(null) });
    expect(prompt).toContain("a clearly named helper");
    expect(extractMethodPlan(methodPlan(null)).suggested_name).toBeNull();
  });

  it("includes the canonical validation plan in copy-to-agent handoff", () => {
    const plan: RefactoringPlan = {
      ...methodPlan("compute_average"),
      validation: {
        basis: "measured",
        via: "coverage",
        total: 1,
        tests: ["tests/test_pipeline.py::test_average"],
        truncated: false,
        affected_files: ["pkg/pipeline.py"],
        affected_symbols: ["run_pipeline"],
        commands: ["pytest tests/test_pipeline.py::test_average"],
        targets: [],
      },
    };
    const prompt = buildRefactoringPlanPrompt({ plan });
    expect(prompt).toContain("## Validation plan");
    expect(prompt).toContain("guarding test via coverage");
    expect(prompt).toContain("tests/test_pipeline.py::test_average");
    expect(prompt).toContain("pytest tests/test_pipeline.py::test_average");
  });
});
