import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
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
    { path: "src/auth/login.py", risk_score: 0.82, temporal_hotspot: 0.74, centrality: 0.045 },
  ],
  transitive_affected: [{ path: "src/api/handlers.py", depth: 2 }],
  cochange_warnings: [
    { changed: "src/auth/login.py", missing_partner: "tests/test_login.py", score: 7 },
  ],
  recommended_reviewers: [{ email: "a@b.co", files: 12, ownership_pct: 0.42 }],
  test_gaps: ["src/auth/login.py"],
  overall_risk_score: 7.6,
};

describe("RiskScoreCard", () => {
  it("labels High Risk for score >= 7", () => {
    render(<RiskScoreCard score={7.6} />);
    expect(screen.getByText("High Risk")).toBeTruthy();
    expect(screen.getByText("7.6")).toBeTruthy();
  });

  it("labels Low Risk for score < 4", () => {
    render(<RiskScoreCard score={2.1} />);
    expect(screen.getByText("Low Risk")).toBeTruthy();
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
  it("scales risk_score 0–1 → 0–10", () => {
    render(<DirectRisksTable rows={fixture.direct_risks} />);
    expect(screen.getByText("8.20")).toBeTruthy();
    expect(screen.getByText("7.40")).toBeTruthy();
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
  it("composes the full results stack", () => {
    render(<BlastRadiusResults result={fixture} />);
    expect(screen.getByText("High Risk")).toBeTruthy();
    // "Direct Risks" / "Co-change Warnings" / "Test Gaps" appear twice
    // (summary stat label + TableSection title) — use getAllByText.
    expect(screen.getAllByText("Direct Risks").length).toBe(2);
    expect(screen.getByText("Transitive Affected Files")).toBeTruthy();
    expect(screen.getAllByText("Co-change Warnings").length).toBe(2);
    expect(screen.getByText("Recommended Reviewers")).toBeTruthy();
    expect(screen.getAllByText("Test Gaps").length).toBe(2);
  });

  it("shows 'None' for empty sections", () => {
    const empty: BlastRadiusResponse = {
      direct_risks: [],
      transitive_affected: [],
      cochange_warnings: [],
      recommended_reviewers: [],
      test_gaps: [],
      overall_risk_score: 1.5,
    };
    render(<BlastRadiusResults result={empty} />);
    expect(screen.getAllByText("None").length).toBe(5);
    expect(screen.getByText("Low Risk")).toBeTruthy();
  });
});
