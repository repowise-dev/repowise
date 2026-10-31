import { describe, expect, it } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import type { BlastRadiusResponse } from "@repowise-dev/types/blast-radius";
import { AffectedByModule, ChangedFilesList } from "../../src/blast-radius/affected-files.js";

const BASE = {
  cochange_warnings: [],
  recommended_reviewers: [],
  structural_impact_score: 1,
  structural_impact_band: "localized",
  structural_impact_scale: {},
  overall_risk_score: 1,
  overall_risk_score_compatibility: {},
} as unknown as BlastRadiusResponse;

const RESULT: BlastRadiusResponse = {
  ...BASE,
  direct_risks: [
    { path: "src/core.py", structural_score: 0.2, risk_score: 0.2, temporal_hotspot: 13.6, churn_percentile: 0.97, is_hotspot: true, centrality: 0.004 },
  ],
  transitive_affected: [
    { path: "src/api/a.py", depth: 1 },
    { path: "src/api/b.py", depth: 2 },
    { path: "web/x.ts", depth: 2 },
  ],
  test_gaps: ["src/api/b.py"],
  affected_signals: [
    { path: "src/api/a.py", is_hotspot: true, health_score: 4.0, module: "src/api" },
    { path: "src/api/b.py", is_hotspot: false, health_score: null, module: "src/api" },
  ],
};

describe("ChangedFilesList", () => {
  it("states churn as a rank, never raw pagerank as a percent", () => {
    render(<ChangedFilesList result={RESULT} changedFiles={["src/core.py"]} fileHref={undefined} />);
    expect(screen.getByText("▲ top 3% churn")).toBeInTheDocument();
    expect(screen.queryByText("▲ hot")).toBeNull();
    expect(screen.queryByText(/0%/)).toBeNull();
  });
});

describe("AffectedByModule", () => {
  it("groups by module, nearest first, and filters", () => {
    render(<AffectedByModule result={RESULT} fileHref={undefined} />);
    const rows = screen.getAllByRole("button", { expanded: false }).map((b) => b.textContent ?? "");
    expect(rows[0]).toContain("src/api");
    expect(rows[0]).toContain("2 files");
    expect(rows[0]).toContain("direct");

    fireEvent.click(screen.getAllByRole("button", { expanded: false })[0]!);
    expect(screen.getByText("a.py")).toBeInTheDocument();

    fireEvent.change(screen.getByRole("searchbox", { name: "Filter affected files" }), { target: { value: "web" } });
    expect(screen.queryByText("src/api")).toBeNull();
    expect(screen.getAllByText("web").length).toBeGreaterThan(0);
    expect(screen.getByText("x.ts")).toBeInTheDocument();
  });
});
