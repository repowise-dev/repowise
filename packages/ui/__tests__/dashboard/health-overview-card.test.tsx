import { describe, expect, it } from "vitest";
import { render } from "@testing-library/react";
import {
  HealthOverviewCard,
  type HealthOverviewData,
} from "../../src/dashboard/health-overview-card";

const data = (over: Partial<HealthOverviewData>): HealthOverviewData => ({
  average_health: 10,
  hotspot_health: null,
  worst_performer_path: null,
  worst_performer_score: null,
  open_findings: 0,
  severity_breakdown: {},
  history: [],
  snapshot_count: 1,
  ...over,
});

describe("HealthOverviewCard band", () => {
  it("shows no band the server withheld", () => {
    const { container } = render(<HealthOverviewCard data={data({ band: null })} repoId="r" />);
    expect(container.textContent).toContain("10.0");
    expect(container.textContent).not.toContain("Excellent");
  });

  it("keeps the band from an older server that sends none", () => {
    const { container } = render(<HealthOverviewCard data={data({})} repoId="r" />);
    expect(container.textContent).toContain("Excellent");
  });
});
