import { describe, it, expect } from "vitest";
import { selectDirectRisks } from "./selectors";
import type { ChangeImpactReport } from "../../../../src/shared/webviewMessages";
import type { DirectRiskEntry } from "@repowise-dev/types/blast-radius";

function report(direct_risks: DirectRiskEntry[] = [], blast = true): ChangeImpactReport {
  return {
    changed: direct_risks.map((d) => d.path),
    stagedCount: 0,
    workingCount: 0,
    scope: "branch",
    reviewers: [],
    gitUnavailable: false,
    blast: blast
      ? {
          direct_risks,
          transitive_affected: [],
          cochange_warnings: [],
          recommended_reviewers: [],
          test_gaps: [],
          overall_risk_score: 0,
        }
      : null,
  };
}

describe("selectDirectRisks", () => {
  it("ranks riskiest first with shares relative to the set maximum", () => {
    const out = selectDirectRisks(
      report([
        { path: "a.ts", risk_score: 0.005, temporal_hotspot: 0, centrality: 0.005 },
        { path: "b.ts", risk_score: 0.02, temporal_hotspot: 0, centrality: 0.02 },
      ]),
    );
    expect(out.map((r) => r.path)).toEqual(["b.ts", "a.ts"]);
    expect(out[0]?.share).toBe(1);
    expect(out[1]?.share).toBeCloseTo(0.25);
  });

  it("flags hotspots only above the floor", () => {
    const out = selectDirectRisks(
      report([
        { path: "hot.ts", risk_score: 0.01, temporal_hotspot: 0.9, centrality: 0.005 },
        { path: "quiet.ts", risk_score: 0.01, temporal_hotspot: 0.2, centrality: 0.005 },
      ]),
    );
    expect(out.find((r) => r.path === "hot.ts")?.hotspot).toBe(true);
    expect(out.find((r) => r.path === "quiet.ts")?.hotspot).toBe(false);
  });

  it("serves zero shares when every risk score is zero", () => {
    const out = selectDirectRisks(
      report([{ path: "a.ts", risk_score: 0, temporal_hotspot: 0, centrality: 0 }]),
    );
    expect(out[0]?.share).toBe(0);
  });

  it("returns nothing when there is no blast payload", () => {
    expect(selectDirectRisks(report([], false))).toEqual([]);
  });
});
