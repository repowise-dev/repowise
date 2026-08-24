import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { Hotspot } from "@repowise-dev/types/git";
import { RiskDistributionChart } from "../../src/git/risk-distribution-chart.js";

const hotspot: Hotspot = {
  file_path: "src/hot.py",
  commit_count_90d: 8,
  commit_count_30d: 3,
  churn_percentile: 80,
  temporal_hotspot_score: 4,
  primary_owner: "Dev",
  is_hotspot: true,
  is_stable: false,
  bus_factor: 1,
  contributor_count: 1,
  lines_added_90d: 20,
  lines_deleted_90d: 5,
  avg_commit_size: 4,
  commit_categories: {},
};

describe("RiskDistributionChart vocabulary", () => {
  it("labels its client-side scalar as an uncalibrated triage heuristic", () => {
    render(<RiskDistributionChart hotspots={[hotspot]} />);

    expect(screen.getByText(/Triage index \(0–100 heuristic\)/)).toBeTruthy();
    expect(screen.getByText(/Uncalibrated; not a probability/)).toBeTruthy();
    expect(screen.queryByText(/Risk Score/)).toBeNull();
  });
});
