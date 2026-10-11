import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import type {
  PatchCoveragePathGate,
  PatchCoverageProject,
  PatchCoverageResponse,
} from "@repowise-dev/types/generated/http";
import {
  PatchCoverageSummary,
  branchText,
  floorPct,
  formatLineRanges,
  hintReason,
  projectText,
  riskWords,
} from "../../src/health/patch-coverage-summary.js";

function coverage(fileCount: number): PatchCoverageResponse {
  return {
    patch_coverage_pct: 50,
    covered_line_count: fileCount,
    coverable_line_count: fileCount * 2,
    threshold: null,
    min_coverable_lines: null,
    gate: "not_set",
    file_counts: {
      measured: fileCount,
      not_in_report: 0,
      no_line_data: 0,
      no_coverable_changes: 0,
      out_of_scope: 0,
    },
    files: Array.from({ length: fileCount }, (_, i) => ({
      file_path: `src/f${i}.ts`,
      status: "measured" as const,
      changed_line_count: 2,
      coverable_line_count: 2,
      covered_line_count: 1,
      patch_coverage_pct: 50,
      uncovered_ranges: [[i + 1, i + 1]],
      branch_taken: 0,
      branch_total: 0,
      partial_ranges: [],
      risk: null,
      hints: null,
    })),
    scope: {
      label: "coverage.xml",
      source_formats: ["cobertura"],
      reports: ["coverage.xml"],
      report_path_count: fileCount,
      unmatched_report_path_count: 0,
      measured_commit: "abcdef1234",
      mapping_partial: false,
      freshness: "current",
      ignored_file_count: 0,
      config_errors: [],
      branch_data: "none",
    },
    path_gates: [],
    risky: null,
    project: null,
    branches: null,
  };
}

function project(base: number, head: number, delta: number): PatchCoverageProject {
  const totals = (pct: number) => ({
    covered_line_count: Math.round(pct * 10),
    coverable_line_count: 1000,
    coverage_pct: pct,
  });
  return {
    basis: "history",
    base_commit: "a1b2c3d".padEnd(40, "0"),
    head_commit: "h".repeat(40),
    base: totals(base),
    head: totals(head),
    delta_pct: delta,
    max_drop: null,
    gate: "not_set",
    incomparable: [],
    outside_change: null,
    outside_change_note: null,
  };
}

function pathGate(
  name: string,
  gate: PatchCoveragePathGate["gate"],
  informational = false,
): PatchCoveragePathGate {
  return {
    name,
    paths: [`/${name}/`],
    threshold: 80,
    informational,
    measured_file_count: 1,
    unmeasured_file_count: 0,
    covered_line_count: 1,
    coverable_line_count: 2,
    patch_coverage_pct: 50,
    gate,
  };
}

function withRisk(): PatchCoverageResponse {
  const base = coverage(2);
  const [plain, hot] = base.files;
  return {
    ...base,
    files: [
      {
        ...plain!,
        risk: {
          fix_pressure: 0,
          dependents: null,
          hotspot: null,
          bug_magnet: null,
          basis: "git",
          risky: false,
          reasons: [],
        },
      },
      {
        ...hot!,
        risk: {
          fix_pressure: 3.24,
          dependents: 14,
          hotspot: true,
          bug_magnet: false,
          basis: "git_and_index",
          risky: true,
          reasons: ["hotspot"],
        },
      },
    ],
    risky: {
      file_count: 1,
      covered_line_count: 1,
      coverable_line_count: 2,
      patch_coverage_pct: 50,
      threshold: null,
      gate: "not_set",
    },
  };
}

describe("patch coverage formatting", () => {
  it("floors to one decimal so a near miss never reads as full", () => {
    expect(floorPct(99.99)).toBe("99.9");
    expect(floorPct(66.66)).toBe("66.6");
    expect(floorPct(57.3)).toBe("57.3");
    expect(floorPct(100)).toBe("100.0");
  });

  const DOWN = "Project coverage 81.2% · down 0.30 points from 81.5% at a1b2c3d";
  it.each([
    [project(81.5, 81.2, -0.3), DOWN],
    [project(80, 80.25, 0.25), "Project coverage 80.2% · up 0.25 points from 80.0% at a1b2c3d"],
    [project(80, 80, 0), "Project coverage 80.0% · unchanged from 80.0% at a1b2c3d"],
    [
      { ...project(81.5, 81.2, -0.3), max_drop: 0.5, gate: "pass" as const },
      `${DOWN} · within the 0.5-point max-drop gate`,
    ],
    [
      { ...project(81.5, 81.2, -0.3), max_drop: 0.1, gate: "fail" as const },
      `${DOWN} · falls more than the 0.1-point max-drop gate allows`,
    ],
    [{ ...project(80, 80, 0), base: null, delta_pct: null }, null],
  ])("words the project delta as the core's project line (%#)", (given, expected) => {
    expect(projectText(given)).toBe(expected);
  });

  it("prints single lines bare and runs as a span", () => {
    expect(
      formatLineRanges([
        [3, 3],
        [7, 9],
      ]),
    ).toBe("3, 7-9");
  });
});

/** The file paths a rendered list shows, in order. */
function listedPaths(container: HTMLElement, list = "li"): (string | null)[] {
  return [...container.querySelectorAll(`${list} .font-mono.flex-1`)].map(
    (el) => el.textContent,
  );
}

describe("PatchCoverageSummary", () => {
  it("lists ten files with gaps and counts the rest", () => {
    const { container } = render(<PatchCoverageSummary coverage={coverage(12)} />);
    // Equal risk and gap size fall back to path order, as the CLI's table does.
    const paths = listedPaths(container);
    expect(paths).toContain("src/f10.ts");
    expect(paths).not.toContain("src/f9.ts");
    expect(screen.getByText("and 2 more")).toBeTruthy();
  });

  it("reports branches on changed lines and lists partly taken files after uncovered ones", () => {
    const base = coverage(2);
    const [uncovered, file] = base.files;
    const branches = {
      branch_taken: 3,
      branch_total: 4,
      branch_coverage_pct: 75,
      partial_line_count: 1,
      threshold: null,
      gate: "not_set" as const,
    };
    const { container } = render(
      <PatchCoverageSummary
        coverage={{
          ...base,
          files: [
            uncovered!,
            {
              ...file!,
              // Sorts first by path, but a partly taken branch comes after an uncovered line.
              file_path: "src/a.ts",
              uncovered_ranges: [],
              branch_taken: 3,
              branch_total: 4,
              partial_ranges: [[12, 12]],
            },
          ],
          branches,
        }}
      />,
    );
    expect(
      screen.getByText("Branches on changed lines 75.0% · 3 of 4 taken · 1 line partly taken"),
    ).toBeTruthy();
    expect(listedPaths(container)).toEqual(["src/f0.ts", "src/a.ts"]);
    expect(screen.getByText("partly taken 12")).toBeTruthy();
    // Not measured is no line at all, never 0%.
    expect(branchText({ ...branches, branch_coverage_pct: null })).toBeNull();
  });

  it("states the denominator and stays quiet when coverage is current", () => {
    render(<PatchCoverageSummary coverage={coverage(1)} />);
    expect(screen.getByText("1 of 2 changed executable lines covered")).toBeTruthy();
    expect(screen.queryByText(/measured at/)).toBeNull();
    expect(screen.queryByText(/ignored by/)).toBeNull();
  });

  it("marks stale coverage and lists what the report does not measure", () => {
    const base = coverage(1);
    render(
      <PatchCoverageSummary
        coverage={{
          ...base,
          files: [
            {
              file_path: "src/core.ts",
              status: "measured",
              changed_line_count: 5,
              coverable_line_count: 3,
              covered_line_count: 2,
              patch_coverage_pct: 66.66,
              uncovered_ranges: [
                [3, 3],
                [7, 9],
              ],
              branch_taken: 0,
              branch_total: 0,
              partial_ranges: [],
              risk: null,
              hints: null,
            },
            {
              file_path: "src/new.ts",
              status: "not_in_report",
              changed_line_count: 4,
              coverable_line_count: 0,
              covered_line_count: 0,
              patch_coverage_pct: null,
              uncovered_ranges: [],
              branch_taken: 0,
              branch_total: 0,
              partial_ranges: [],
              risk: null,
              hints: null,
            },
          ],
          scope: { ...base.scope, freshness: "stale" },
        }}
      />,
    );
    expect(screen.getByText("abcdef1")).toBeTruthy();
    expect(screen.getByText(/not at this change's head/)).toBeTruthy();
    expect(screen.getByText("lines 3, 7-9")).toBeTruthy();
    expect(screen.getByText("not in report")).toBeTruthy();
  });

  it("counts changed files coverage.ignore left out", () => {
    const base = coverage(1);
    render(
      <PatchCoverageSummary
        coverage={{ ...base, scope: { ...base.scope, ignored_file_count: 2 } }}
      />,
    );
    expect(screen.getByText(/2 changed files ignored by/)).toBeTruthy();
  });

  it("lists path-scoped gates with a verdict in words", () => {
    render(
      <PatchCoverageSummary
        coverage={{
          ...coverage(1),
          path_gates: [pathGate("api", "fail"), pathGate("docs", "fail", true)],
        }}
      />,
    );
    const list = screen.getByRole("list", { name: "Path-scoped gates" });
    expect(list.textContent).toContain("api");
    expect(screen.getByText("fails")).toBeTruthy();
    // An informational miss never reads as a failure.
    expect(screen.getByText("below threshold (informational)")).toBeTruthy();
    expect(screen.getAllByText("1 of 2 (50.0%) · gate 80.0%")).toHaveLength(2);
    // A visible label heads the list.
    expect(screen.getByText("Path-scoped gates")).toBeTruthy();
  });

  it("says why a gate is not judged and counts unmeasured files", () => {
    const base = coverage(1);
    render(
      <PatchCoverageSummary
        coverage={{
          ...base,
          scope: { ...base.scope, config_errors: ["coverage.gates[1]: bad"] },
          path_gates: [
            pathGate("api", "no_data"),
            {
              ...pathGate("new", "no_data"),
              covered_line_count: 0,
              coverable_line_count: 0,
              patch_coverage_pct: null,
              unmeasured_file_count: 2,
            },
          ],
        }}
      />,
    );
    expect(screen.getByText(/1 invalid entry in/)).toBeTruthy();
    expect(screen.getByText("not judged")).toBeTruthy();
    expect(
      screen.getByText("no measured changed lines (2 changed files not measured)"),
    ).toBeTruthy();
  });

  it("compares project coverage with the base under the headline", () => {
    render(
      <PatchCoverageSummary coverage={{ ...coverage(1), project: project(81.5, 81.2, -0.3) }} />,
    );
    expect(
      screen.getByText("Project coverage 81.2% · down 0.30 points from 81.5% at a1b2c3d"),
    ).toBeTruthy();
  });

  it("says in muted text why project coverage was not compared", () => {
    const incomparable = {
      ...project(81.5, 81.2, -0.3),
      incomparable: ["the base read 1 lcov report, the head read 2 lcov reports"],
    };
    render(<PatchCoverageSummary coverage={{ ...coverage(1), project: incomparable }} />);
    const line = screen.getByText(/Project coverage not compared: the base read 1 lcov/);
    expect(line.className).toContain("text-tertiary");
    expect(screen.queryByText(/down 0.30 points/)).toBeNull();
  });

  it("shows no project line without a base measurement", () => {
    render(<PatchCoverageSummary coverage={coverage(1)} />);
    expect(screen.queryByText(/Project coverage/)).toBeNull();
  });

  it("shows no gate list without path-scoped gates", () => {
    render(<PatchCoverageSummary coverage={coverage(1)} />);
    expect(screen.queryByRole("list", { name: "Path-scoped gates" })).toBeNull();
    expect(screen.queryByText(/invalid entr/)).toBeNull();
  });

  it("renders a response from a server older than path-scoped gates and risk", () => {
    const base = coverage(1);
    const { path_gates: _gates, risky: _risky, project: _project, ...older } = base;
    const { config_errors: _errors, ...olderScope } = base.scope;
    render(
      <PatchCoverageSummary
        coverage={{ ...older, scope: olderScope } as unknown as PatchCoverageResponse}
      />,
    );
    expect(screen.getByText("1 of 2 changed executable lines covered")).toBeTruthy();
    expect(screen.queryByRole("list", { name: "Path-scoped gates" })).toBeNull();
  });

  it("lists the risky file first and names its risk in words", () => {
    const { container } = render(<PatchCoverageSummary coverage={withRisk()} />);
    expect(listedPaths(container)).toEqual(["src/f1.ts", "src/f0.ts"]);
    expect(screen.getByText("hotspot, bug-fix weight 3.2, 14 dependents")).toBeTruthy();
    // "none known" is not worth a label on a row.
    expect(screen.queryByText("none known")).toBeNull();
    expect(
      screen.getByText(/Risky files 50\.0% · 1 of 2 changed executable lines covered/),
    ).toBeTruthy();
    // One row had no index data, so the basis is disclosed.
    expect(
      screen.getByText(/Risk for 1 of 2 files is from git bug-fix history alone/),
    ).toBeTruthy();
  });

  it("orders unmeasured files as the core does: not in report first, then by risk", () => {
    const base = withRisk();
    const [plain, hot] = base.files;
    const unmeasured = (
      path: string,
      status: "not_in_report" | "no_line_data",
      file: typeof plain,
    ) => ({
      ...file!,
      file_path: path,
      status,
      coverable_line_count: 0,
      covered_line_count: 0,
      patch_coverage_pct: null,
      uncovered_ranges: [],
    });
    const { container } = render(
      <PatchCoverageSummary
        coverage={{
          ...base,
          files: [
            unmeasured("a_nodata_hot.ts", "no_line_data", hot),
            unmeasured("b_new_plain.ts", "not_in_report", plain),
            unmeasured("c_new_hot.ts", "not_in_report", hot),
          ],
        }}
      />,
    );
    expect(listedPaths(container, "details li")).toEqual([
      "c_new_hot.ts",
      "b_new_plain.ts",
      "a_nodata_hot.ts",
    ]);
    expect(screen.getAllByText("not in report")).toHaveLength(2);
  });

  it("names the test to extend from the file's first hint that has one", () => {
    const base = coverage(2);
    const [withHint, without] = base.files;
    render(
      <PatchCoverageSummary
        coverage={{
          ...base,
          files: [
            {
              ...withHint!,
              hints: [
                { range: [1, 1], symbol: null, tests: [], basis: "none", total: 0 },
                {
                  range: [4, 5],
                  symbol: "login",
                  tests: ["tests/test_auth.py", "tests/test_session.py"],
                  basis: "call_graph",
                  total: 2,
                },
              ],
            },
            // An index with nothing to suggest shows no line at all.
            { ...without!, hints: [] },
          ],
        }}
      />,
    );
    expect(screen.getByText("tests/test_auth.py")).toBeTruthy();
    const line = screen.getByTitle(
      "extend tests/test_auth.py (inferred: calls reach login)",
    );
    expect(line.className).toContain("text-xs");
    expect(screen.queryByText("tests/test_session.py")).toBeNull();
    expect(screen.getAllByText(/^extend/)).toHaveLength(1);
  });

  it("says so when the index names no test", () => {
    const base = coverage(1);
    render(
      <PatchCoverageSummary
        coverage={{
          ...base,
          files: [
            {
              ...base.files[0]!,
              hints: [{ range: [1, 1], symbol: null, tests: [], basis: "none", total: 0 }],
            },
          ],
        }}
      />,
    );
    expect(screen.getByText("no test reaches this; add one")).toBeTruthy();
  });
});

describe("hintReason", () => {
  it("says which evidence named the test, measured or inferred", () => {
    const hint = { range: [1, 2], symbol: "run", tests: ["t.py"], total: 1 };
    expect(hintReason({ ...hint, basis: "per_test" })).toBe(
      "measured: runs other lines of run",
    );
    expect(hintReason({ ...hint, symbol: null, basis: "per_test" })).toBe(
      "measured: runs nearby lines",
    );
    expect(hintReason({ ...hint, basis: "call_graph" })).toBe("inferred: calls reach run");
    expect(hintReason({ ...hint, basis: "import_graph" })).toBe(
      "inferred: imports this file",
    );
    expect(hintReason({ ...hint, tests: [], basis: "none" })).toBe(
      "no test reaches this; add one",
    );
  });
});

describe("riskWords", () => {
  it("reads unassessed risk as nothing, unreadable as unknown, and names the weight", () => {
    const risk = {
      fix_pressure: 1,
      dependents: 1,
      hotspot: null,
      bug_magnet: null,
      basis: "git" as const,
      risky: false,
      reasons: [],
    };
    expect(riskWords(null)).toBeNull();
    expect(riskWords({ ...risk, basis: "unavailable" })).toBe("unknown");
    expect(riskWords(risk)).toBe("bug-fix weight 1.0, 1 dependent");
    expect(riskWords({ ...risk, fix_pressure: 0, dependents: null })).toBe("none known");
  });
});
