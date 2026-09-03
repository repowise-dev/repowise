import { describe, it, expect } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { RiskScoreCard } from "../../src/blast-radius/risk-score-card";
import { TableSection } from "../../src/blast-radius/table-section";
import { DirectRisksTable } from "../../src/blast-radius/direct-risks-table";
import { TransitiveTable } from "../../src/blast-radius/transitive-table";
import { CochangeTable } from "../../src/blast-radius/cochange-table";
import { ReviewersTable } from "../../src/blast-radius/reviewers-table";
import { TestGapsList } from "../../src/blast-radius/test-gaps-list";
import { BlastRadiusSummary } from "../../src/blast-radius/blast-radius-summary";
import { BlastRadiusResults } from "../../src/blast-radius/blast-radius-results";
import type { BlastRadiusResponse } from "@repowise-dev/types/blast-radius";

const fixture: BlastRadiusResponse = {
  direct_risks: [
    {
      path: "src/auth/login.py",
      structural_score: 0.82,
      risk_score: 0.82,
      temporal_hotspot: 0.74,
      centrality: 0.045,
    },
  ],
  transitive_affected: [{ path: "src/api/handlers.py", depth: 2 }],
  cochange_warnings: [
    {
      changed: "src/auth/login.py",
      missing_partner: "tests/test_login.py",
      score: 7,
    },
  ],
  recommended_reviewers: [{ email: "a@b.co", files: 12, ownership_pct: 0.42 }],
  test_gaps: ["src/auth/login.py"],
  test_impact: {
    recommendations: [
      {
        test_id: "tests/test_login.py::test_login",
        test_file: "tests/test_login.py",
        repository_id: "repo-1",
        repository: "app",
        basis: "measured",
        bases: ["measured"],
        source_files: ["src/auth/login.py"],
        evidence: [
          {
            basis: "measured",
            source_file: "src/auth/login.py",
            via: "coverage-map",
            source_format: "coverage.py",
          },
        ],
      },
    ],
    recommendations_total: 1,
    recommendations_emitted: 1,
    recommendations_truncated: false,
    recommendations_omitted: 0,
    recommendations_by_primary_basis: { measured: 1, inferred: 0 },
    files: [
      {
        source_file: "src/auth/login.py",
        status: "measured",
        measured_tests: ["tests/test_login.py::test_login"],
        measured_tests_total: 1,
        inferred_tests: [],
        inferred_tests_total: 0,
      },
    ],
    files_total: 1,
    files_without_measured_tests: [],
    unknown_files: [],
    coverage: {
      status: "available",
      reason: null,
      map_present: true,
      pair_count: 1,
      test_count: 1,
      source_file_count: 1,
      changed_files_total: 1,
      changed_files_with_measured_tests: 1,
      changed_files_without_measured_tests: 0,
      ingested_at: "2026-08-24T00:00:00Z",
      source_format: "coverage.py",
      freshness: {
        status: "current",
        reason: null,
        ingested_commit: "abc",
        indexed_commit: "abc",
      },
    },
    inference: {
      status: "available",
      reason: null,
      changed_files_total: 1,
      changed_files_with_candidates: 0,
      candidates_before_dedup: 0,
    },
    analysis: {
      status: "available",
      stale: false,
      partial: false,
      degraded: false,
      basis_categories: ["measured"],
    },
  },
  structural_impact_score: 7.6,
  structural_impact_band: "broad",
  structural_impact_scale: {
    field: "structural_impact_score",
    kind: "heuristic_structural_score",
    unit: "normalized_points",
    range: { minimum: 0, maximum: 10 },
    measures: "indexed structural exposure",
    deterministic: true,
    calibration: { status: "uncalibrated", source: null },
    authoritative_for_change_review: false,
    runtime_breakage_probability: false,
  },
  overall_risk_score: 7.6,
  overall_risk_score_compatibility: {
    deprecated: true,
    replacement: "structural_impact_score",
    equivalent_value: true,
    historical_meaning: "uncalibrated 0-10 structural blast-radius heuristic",
  },
};

describe("RiskScoreCard", () => {
  it("uses the server-provided broad structural band", () => {
    render(<RiskScoreCard score={7.6} band="broad" />);
    expect(screen.getByText("Broad structural impact")).toBeTruthy();
    expect(screen.getByText("7.6")).toBeTruthy();
  });

  it("does not derive a risk band when a legacy caller omits one", () => {
    render(<RiskScoreCard score={2.1} />);
    expect(screen.getByText("Structural impact")).toBeTruthy();
  });
});

describe("TableSection", () => {
  it("renders empty copy when empty=true", () => {
    render(
      <TableSection title="Direct Risks" empty>
        <div>hidden</div>
      </TableSection>,
    );
    expect(screen.getByText("None")).toBeTruthy();
    expect(screen.queryByText("hidden")).toBeNull();
  });

  it("renders children when empty=false", () => {
    render(
      <TableSection title="Direct Risks" empty={false}>
        <div>shown</div>
      </TableSection>,
    );
    expect(screen.getByText("shown")).toBeTruthy();
  });
});

describe("DirectRisksTable", () => {
  it("shows the raw structural weight instead of inventing a 0–10 scale", () => {
    render(<DirectRisksTable rows={fixture.direct_risks} />);
    expect(screen.getByText("0.8200")).toBeTruthy();
    expect(screen.getByText("7.4")).toBeTruthy();
  });
});

describe("TransitiveTable", () => {
  it("renders depth", () => {
    render(<TransitiveTable rows={fixture.transitive_affected} />);
    expect(screen.getByText("2")).toBeTruthy();
  });
});

describe("CochangeTable", () => {
  it("renders score", () => {
    render(<CochangeTable rows={fixture.cochange_warnings} />);
    expect(screen.getByText("tests/test_login.py")).toBeTruthy();
    expect(screen.getByText("7")).toBeTruthy();
  });
});

describe("ReviewersTable", () => {
  it("formats ownership_pct as percent", () => {
    render(<ReviewersTable rows={fixture.recommended_reviewers} />);
    expect(screen.getByText("42.0%")).toBeTruthy();
  });
});

describe("TestGapsList", () => {
  it("renders each gap", () => {
    render(<TestGapsList gaps={fixture.test_gaps} />);
    expect(screen.getByText("src/auth/login.py")).toBeTruthy();
  });
});

describe("BlastRadiusSummary", () => {
  it("renders four counts", () => {
    render(<BlastRadiusSummary result={fixture} />);
    expect(screen.getByText("Direct Risks")).toBeTruthy();
    expect(screen.getByText("Transitive Files")).toBeTruthy();
    expect(screen.getByText("Co-change Warnings")).toBeTruthy();
    expect(screen.getByText("Test Gaps")).toBeTruthy();
  });
});

describe("BlastRadiusResults", () => {
  it("composes the risk header, impact map, and collapsible sections", () => {
    render(
      <BlastRadiusResults
        result={fixture}
        changedFiles={["src/auth/login.py"]}
      />,
    );
    // Header band label + gauge score.
    expect(screen.getByText("Broad structural impact")).toBeTruthy();
    expect(screen.getByText("7.6")).toBeTruthy();
    // The header tile and the collapsible toggle share the "Direct risks" /
    // "Test gaps" copy, so each appears more than once.
    expect(screen.getAllByText("Direct risks").length).toBeGreaterThanOrEqual(
      1,
    );
    expect(screen.getByText("Transitive affected files")).toBeTruthy();
    expect(screen.getByText("Co-change warnings")).toBeTruthy();
    expect(screen.getByText("Recommended reviewers")).toBeTruthy();
    expect(screen.getAllByText("Test gaps").length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText("Impact map")).toBeTruthy();
    expect(screen.getByText("tests/test_login.py::test_login")).toBeTruthy();
    expect(screen.getByText(/coverage-backed/)).toBeTruthy();
  });

  it("shows an empty impact map when nothing is affected", () => {
    const empty: BlastRadiusResponse = {
      ...fixture,
      direct_risks: [],
      transitive_affected: [],
      cochange_warnings: [],
      recommended_reviewers: [],
      test_gaps: [],
      structural_impact_score: 1.5,
      structural_impact_band: "localized",
      overall_risk_score: 1.5,
    };
    render(<BlastRadiusResults result={empty} />);
    expect(screen.getByText("No downstream impact found")).toBeTruthy();
    expect(screen.getByText("Localized structural impact")).toBeTruthy();
  });

  it("keeps older server payloads compatible without assuming test availability", () => {
    const { test_impact: _testImpact, ...legacy } = fixture;

    render(
      <BlastRadiusResults
        result={legacy as BlastRadiusResponse}
        changedFiles={["src/auth/login.py"]}
      />,
    );
    expect(
      screen.getByText(/did not provide typed test-analysis state/),
    ).toBeTruthy();
  });

  it("labels partial test analysis even when recommendations are present", () => {
    const partial: BlastRadiusResponse = {
      ...fixture,
      test_impact: {
        ...fixture.test_impact!,
        coverage: {
          ...fixture.test_impact!.coverage,
          status: "partial",
          reason: "coverage_map_matches_only_part_of_change",
        },
        analysis: {
          ...fixture.test_impact!.analysis,
          status: "partial",
          partial: true,
        },
      },
    };

    render(
      <BlastRadiusResults
        result={partial}
        changedFiles={["src/auth/login.py"]}
      />,
    );
    expect(
      screen.getAllByText(/Test analysis is partial/).length,
    ).toBeGreaterThan(0);
  });

  it("does not interpret unavailable coverage as no tests needed", () => {
    const unavailable: BlastRadiusResponse = {
      ...fixture,
      test_gaps: [],
      test_impact: {
        ...fixture.test_impact!,
        recommendations: [],
        recommendations_total: 0,
        recommendations_emitted: 0,
        recommendations_by_primary_basis: { measured: 0, inferred: 0 },
        coverage: {
          ...fixture.test_impact!.coverage,
          status: "unavailable",
          reason: "no_per_test_coverage_map",
          map_present: false,
        },
        analysis: {
          ...fixture.test_impact!.analysis,
          status: "partial",
          partial: true,
          basis_categories: [],
        },
      },
    };

    render(<BlastRadiusResults result={unavailable} />);
    expect(screen.getByText("Test analysis limited")).toBeTruthy();
    expect(
      screen.getAllByText(/does not mean no tests are needed/).length,
    ).toBeGreaterThan(0);
  });
});
