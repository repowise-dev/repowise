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

describe("HealthOverviewCard performance", () => {
  const perf = {
    maintainability_average: 8.1,
    performance_average: 9.76,
  };

  it("leads with the causes worth fixing now and keeps production as context", () => {
    const { container } = render(
      <HealthOverviewCard
        data={data({
          ...perf,
          performance_causes: { now: 4, production: 415, all: 926, per_10k_loc: 9.03 },
        })}
        repoId="r"
      />,
    );
    const text = container.textContent ?? "";
    expect(text).toContain("4worth fixing now");
    expect(text).toContain("415 in production");
    expect(text).not.toContain("926");
  });

  it("is all clear only when no production cause is open", () => {
    const waiting = render(
      <HealthOverviewCard
        data={data({
          ...perf,
          performance_causes: { now: 0, production: 12, all: 20, per_10k_loc: 1 },
        })}
        repoId="r"
      />,
    );
    expect(waiting.container.textContent).toContain("0worth fixing now");
  });

  it("shows an older payload's open count rather than all clear", () => {
    const { container } = render(
      <HealthOverviewCard data={data({ ...perf, performance_findings: 942 })} repoId="r" />,
    );
    const text = container.textContent ?? "";
    expect(text).toContain("942open");
    expect(text).not.toContain("worth fixing now");
    expect(text).not.toContain("in production");
  });
});
