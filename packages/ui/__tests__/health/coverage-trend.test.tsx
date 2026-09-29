import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import type { CoverageHistoryPoint, CoverageSummary } from "@repowise-dev/types/health";
import { CoverageLede } from "../../src/health/coverage-lede.js";

const SUMMARY: CoverageSummary = {
  file_count: 2,
  covered_lines: 30,
  total_lines: 40,
  line_coverage_pct: 75,
  branch_coverage_pct: null,
  source_format: "lcov",
  source_formats: ["lcov"],
  mapping_partial: false,
  ingested_at: "2026-09-28T10:00:00Z",
  ingested_commit_sha: "aaaaaaaa11",
  report_paths: null,
  freshness: { status: "current", indexed_commit: "aaaaaaaa11" },
};

function points(...pcts: number[]): CoverageHistoryPoint[] {
  return pcts.map((pct, i) => ({
    ingested_at: `2026-09-2${i}T10:00:00Z`,
    ingested_commit_sha: `c${i}`,
    line_coverage_pct: pct,
    branch_coverage_pct: null,
  }));
}

function renderLede(history?: CoverageHistoryPoint[]) {
  return render(
    <CoverageLede summary={SUMMARY} files={[]} moduleCount={0} history={history} />,
  );
}

describe("CoverageLede trend", () => {
  it("draws nothing for fewer than three reports", () => {
    const { container } = renderLede(points(70, 75));
    expect(container.querySelector("svg path")).toBeNull();
    expect(screen.queryByText(/across the last/)).not.toBeInTheDocument();
  });

  it("draws nothing without a history", () => {
    const { container } = renderLede();
    expect(container.querySelector("svg path")).toBeNull();
  });

  it("draws the line and says which way it moved from three reports", () => {
    const { container } = renderLede(points(71.8, 73, 75));
    expect(container.querySelector("svg path")).not.toBeNull();
    expect(
      screen.getByText("Up 3.2 points across the last 3 reports, from 71.8% to 75.0%."),
    ).toBeInTheDocument();
  });

  it("names a fall in words", () => {
    renderLede(points(80, 78, 75));
    expect(
      screen.getByText("Down 5.0 points across the last 3 reports, from 80.0% to 75.0%."),
    ).toBeInTheDocument();
  });

  it("measures the move between the figures it prints", () => {
    // 71.84 and 75.06 print as 71.8 and 75.1: the gap is 3.3, not 3.22.
    renderLede(points(71.84, 73, 75.06));
    expect(
      screen.getByText("Up 3.3 points across the last 3 reports, from 71.8% to 75.1%."),
    ).toBeInTheDocument();
  });

  it("calls a flat series unchanged", () => {
    renderLede(points(75, 75.01, 75));
    expect(screen.getByText("Unchanged at 75.0% across the last 3 reports.")).toBeInTheDocument();
  });

  it("says back at when the series moved and returned", () => {
    renderLede(points(75, 74, 75));
    expect(screen.getByText("Back at 75.0% across the last 3 reports.")).toBeInTheDocument();
  });

  it("hides the trend under a partial headline", () => {
    const { container } = render(
      <CoverageLede
        summary={{ ...SUMMARY, mapping_partial: true }}
        files={[]}
        moduleCount={0}
        history={points(70, 72, 75)}
      />,
    );
    expect(container.querySelector("svg path")).toBeNull();
    expect(screen.queryByText(/across the last/)).not.toBeInTheDocument();
  });
});
