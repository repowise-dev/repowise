import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import type { PatchCoverageResponse } from "@repowise-dev/types/generated/http";
import {
  PatchCoverageSummary,
  floorPct,
  formatLineRanges,
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

describe("PatchCoverageSummary", () => {
  it("lists ten files with gaps and counts the rest", () => {
    render(<PatchCoverageSummary coverage={coverage(12)} />);
    expect(screen.getByText("src/f9.ts")).toBeTruthy();
    expect(screen.queryByText("src/f10.ts")).toBeNull();
    expect(screen.getByText("and 2 more")).toBeTruthy();
  });

  it("stays quiet about freshness when coverage is current", () => {
    render(<PatchCoverageSummary coverage={coverage(1)} />);
    expect(screen.getByText("1 of 2 changed executable lines covered")).toBeTruthy();
    expect(screen.queryByText(/measured at/)).toBeNull();
    expect(screen.queryByText(/ignored by/)).toBeNull();
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
});
