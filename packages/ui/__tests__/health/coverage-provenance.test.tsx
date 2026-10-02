import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { SWRConfig } from "swr";
import type { CoverageSummary, HealthCoverageResponse } from "@repowise-dev/types/health";
import { CoverageLede } from "../../src/health/coverage-lede.js";
import { CoverageView } from "../../src/health/coverage-view.js";
import type { CodeHealthAdapter } from "../../src/health/code-health-adapter.js";

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
  report_paths: {
    total: 5,
    matched: 2,
    unmatched: 3,
    ambiguous: 0,
    unmatched_sample: ["/ci/build/src/gone.py"],
  },
  freshness: { status: "current", indexed_commit: "aaaaaaaa11" },
};

describe("CoverageLede freshness", () => {
  it("stays quiet when the report was measured at the indexed commit", () => {
    render(<CoverageLede summary={SUMMARY} files={[]} moduleCount={0} />);
    expect(screen.queryByText("Measured at another commit.")).not.toBeInTheDocument();
  });

  it("says so when the report came from another commit", () => {
    render(
      <CoverageLede
        summary={{
          ...SUMMARY,
          freshness: { status: "stale", indexed_commit: "bbbbbbbb22" },
        }}
        files={[]}
        moduleCount={0}
      />,
    );
    expect(screen.getByText("Measured at another commit.")).toBeInTheDocument();
    expect(screen.getByText("bbbbbbbb")).toBeInTheDocument();
  });
});

describe("CoverageGap report-path diagnostic", () => {
  it("states how many report paths matched, with an example miss", async () => {
    const response: HealthCoverageResponse = {
      basis: "measured",
      summary: SUMMARY,
      files: [],
      modules: [],
      modules_total: 0,
      inferred: {
        files: [],
        files_total: 1,
        files_reached: 1,
        files_not_reached: 0,
        test_file_count: 1,
        measured_file_count: 2,
      },
    };
    const adapter = {
      cacheKey: "provenance-test",
      getCoverage: vi.fn().mockResolvedValue(response),
      listFindings: vi.fn().mockResolvedValue([]),
      navigate: vi.fn(),
      fileHref: (p: string) => `/files/${p}`,
    } as unknown as CodeHealthAdapter;

    render(
      <SWRConfig value={{ provider: () => new Map(), dedupingInterval: 0 }}>
        <CoverageView adapter={adapter} />
      </SWRConfig>,
    );

    expect(await screen.findByText("2 of 5")).toBeInTheDocument();
    expect(screen.getByText("/ci/build/src/gone.py")).toBeInTheDocument();
  });
});
