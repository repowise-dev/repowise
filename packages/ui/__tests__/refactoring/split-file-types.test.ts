import { describe, it, expect } from "vitest";
import {
  blastCount,
  blastFiles,
  generatedVerdict,
  planSynopsis,
  planWins,
  splitBlast,
  splitGroups,
  splitResidual,
  splitShimRequired,
  type GeneratedCode,
  type RefactoringPlan,
} from "../../src/refactoring/types";
import { typeMeta, TYPE_ORDER } from "../../src/refactoring/meta";

function splitPlan(overrides: Partial<RefactoringPlan> = {}): RefactoringPlan {
  return {
    id: "p1",
    refactoring_type: "split_file",
    file_path: "pkg/big.py",
    target_symbol: "big.py -> 2 files",
    line_start: null,
    line_end: null,
    plan: {
      groups: [
        { name: "parsing", symbols: ["parse_a", "parse_b"], suggested_file: "pkg/parsing.py" },
        { name: "rendering", symbols: ["render_a"], suggested_file: "pkg/rendering.py" },
      ],
      residual: { symbols: ["shared_helper"] },
      shim_required: true,
    },
    evidence: { file_nloc: 400, symbol_count: 8, group_count: 2, modularity: 0.5 },
    impact_delta: 0,
    effort_bucket: "L",
    blast_radius: { dependent_files: ["pkg/user.py", "pkg/other.py"], dependent_count: 2, import_rewrites: 2 },
    confidence: "high",
    source_biomarker: "",
    rank_score: 3.2,
    ...overrides,
  };
}

describe("split_file plan accessors", () => {
  it("reads groups, residual, and shim flag", () => {
    const plan = splitPlan();
    const groups = splitGroups(plan);
    expect(groups).toHaveLength(2);
    expect(groups[0]).toMatchObject({ name: "parsing", suggested_file: "pkg/parsing.py" });
    expect(groups[0]!.symbols).toEqual(["parse_a", "parse_b"]);
    expect(splitResidual(plan)).toEqual(["shared_helper"]);
    expect(splitShimRequired(plan)).toBe(true);
  });

  it("reads the dependent-file blast radius", () => {
    const blast = splitBlast(splitPlan());
    expect(blast.dependent_count).toBe(2);
    expect(blast.import_rewrites).toBe(2);
    expect(blast.dependent_files).toEqual(["pkg/user.py", "pkg/other.py"]);
  });

  it("feeds the generic blast helpers off the split shape", () => {
    const plan = splitPlan();
    expect(blastFiles(plan)).toEqual(["pkg/user.py", "pkg/other.py"]);
    expect(blastCount(plan)).toBe(2);
  });

  it("degrades gracefully on a malformed plan", () => {
    const plan = splitPlan({ plan: {}, blast_radius: {} });
    expect(splitGroups(plan)).toEqual([]);
    expect(splitResidual(plan)).toEqual([]);
    expect(splitShimRequired(plan)).toBe(false);
    expect(splitBlast(plan)).toEqual({ dependent_files: [], dependent_count: 0, import_rewrites: 0 });
  });

  it("summarizes for the card and the win band", () => {
    const plan = splitPlan();
    expect(planSynopsis(plan)).toBe("Split into 2 modules");
    const wins = planWins(plan).map((w) => w.label);
    expect(wins).toContain("2 focused modules from one file");
    expect(wins).toContain("2 dependent files to re-point");
  });

  it("frames a zero-import-edit (Go) split as a win", () => {
    const plan = splitPlan({
      plan: { groups: [{ name: null, symbols: ["a"], suggested_file: "pkg/a.go" }], residual: null, shim_required: false },
      blast_radius: { dependent_files: ["pkg/x.go"], dependent_count: 1, import_rewrites: 0 },
    });
    expect(planWins(plan).map((w) => w.label)).toContain("1 dependent, zero import edits");
  });
});

describe("split_file meta + self-check verdict", () => {
  it("has its own label, icon, and place in the legend order", () => {
    expect(typeMeta("split_file").label).toBe("Split File");
    expect(TYPE_ORDER).toContain("split_file");
  });

  it("reads a clean partition as a pass", () => {
    const result = {
      refactoring_type: "split_file",
      validation: { status: "checked", file_count: 2, max_file_nloc: 120, duplicated_symbols: [], improved: true },
    } as unknown as GeneratedCode;
    const verdict = generatedVerdict(result);
    expect(verdict).toMatchObject({ tone: "pass", label: "Cleanly partitioned" });
    expect(verdict?.detail).toContain("2 files");
  });

  it("reads a duplicated symbol as a fail", () => {
    const result = {
      refactoring_type: "split_file",
      validation: { status: "checked", file_count: 2, max_file_nloc: 120, duplicated_symbols: ["parse_a"], improved: false },
    } as unknown as GeneratedCode;
    const verdict = generatedVerdict(result);
    expect(verdict).toMatchObject({ tone: "fail", label: "Partition incomplete" });
    expect(verdict?.detail).toContain("1 symbol duplicated");
  });
});
