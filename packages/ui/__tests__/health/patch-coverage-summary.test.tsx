import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import type {
  PatchCoveragePathGate,
  PatchCoverageResponse,
} from "@repowise-dev/types/generated/http";
import {
  PatchCoverageSummary,
  floorPct,
  formatLineRanges,
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
      risk: null,
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
    },
    path_gates: [],
    risky: null,
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
              risk: null,
            },
            {
              file_path: "src/new.ts",
              status: "not_in_report",
              changed_line_count: 4,
              coverable_line_count: 0,
              covered_line_count: 0,
              patch_coverage_pct: null,
              uncovered_ranges: [],
              risk: null,
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

  it("shows no gate list without path-scoped gates", () => {
    render(<PatchCoverageSummary coverage={coverage(1)} />);
    expect(screen.queryByRole("list", { name: "Path-scoped gates" })).toBeNull();
    expect(screen.queryByText(/invalid entr/)).toBeNull();
  });

  it("renders a response from a server older than path-scoped gates and risk", () => {
    const base = coverage(1);
    const { path_gates: _gates, risky: _risky, ...older } = base;
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
