import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import type { PeriodHealthSummaryResponse } from "@repowise-dev/types/history";
import { PeriodLede, readPeriodWork } from "../../src/commits/period-lede.js";

function summary(over: Partial<PeriodHealthSummaryResponse & { categories: Record<string, number> }> = {}) {
  return {
    total: 100,
    improved: 10,
    mixed: 2,
    worsened: 5,
    neutral: 3,
    unscanned: 80,
    findings_resolved: 20,
    findings_introduced: 6,
    findings_worsened: 2,
    net_findings: -12,
    categories: { fix: 57, feature: 25, docs: 18 },
    ...over,
  } as PeriodHealthSummaryResponse & { categories: Record<string, number> };
}

describe("readPeriodWork", () => {
  it("names the dominant work and which way health moved", () => {
    const r = readPeriodWork(summary());
    expect(r.top).toEqual({ category: "fix", count: 57, share: 57 });
    expect(r.second).toEqual({ category: "feature", share: 25 });
    expect(r.direction).toBe("better");
  });

  it("drops a small runner-up and says nothing about health with nothing analysed", () => {
    const r = readPeriodWork(
      summary({ categories: { fix: 90, docs: 10 }, improved: 0, mixed: 0, worsened: 0, neutral: 0, unscanned: 100 }),
    );
    expect(r.second).toBeNull();
    expect(r.direction).toBe("unknown");
  });
});

describe("PeriodLede", () => {
  it("leads with volume and work mix, and keeps the coverage caveat as a quiet line", () => {
    render(<PeriodLede summary={summary()} period="Past month" />);
    expect(screen.getByText("100")).toBeInTheDocument();
    expect(screen.getByText(/57% were fixes, then features at 25%/)).toBeInTheDocument();
    expect(screen.getByText(/20 findings resolved or improved, 8 added or worsened/)).toBeInTheDocument();
    expect(screen.getByText(/cover the 20 of 100 commits/)).toBeInTheDocument();
  });

  it("drops the coverage line when everything is analysed", () => {
    render(<PeriodLede summary={summary({ unscanned: 0, neutral: 83 })} period="Past month" />);
    expect(screen.queryByText(/current analyzer has read/)).toBeNull();
  });
});
