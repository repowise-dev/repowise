import { describe, it, expect } from "vitest";
import {
  changeSignature,
  selectMissingCochanges,
} from "../../src/shared/changeImpact";
import type { ChangeImpactReport } from "../../src/shared/webviewMessages";

function report(
  overrides: Partial<ChangeImpactReport> & {
    cochange_warnings?: Array<{
      changed: string;
      missing_partner: string;
      score: number;
    }>;
  } = {},
): ChangeImpactReport {
  const { cochange_warnings = [], ...rest } = overrides;
  return {
    changed: [],
    stagedCount: 0,
    workingCount: 0,
    scope: "working",
    reviewers: [],
    gitUnavailable: false,
    blast: {
      direct_risks: [],
      transitive_affected: [],
      cochange_warnings,
      recommended_reviewers: [],
      test_gaps: [],
      overall_risk_score: 0,
    },
    ...rest,
  };
}

describe("selectMissingCochanges", () => {
  it("groups by partner and keeps the strongest score", () => {
    const out = selectMissingCochanges(
      report({
        changed: ["a.ts"],
        cochange_warnings: [
          { changed: "a.ts", missing_partner: "b.ts", score: 5 },
          { changed: "a.ts", missing_partner: "b.ts", score: 9 },
          { changed: "a.ts", missing_partner: "c.ts", score: 6 },
        ],
      }),
      4,
    );
    expect(out).toHaveLength(2);
    // Sorted by score desc; b.ts keeps the max (9) and counts both pairs.
    expect(out[0]).toMatchObject({ partner: "b.ts", score: 9, count: 2 });
    expect(out[1]?.partner).toBe("c.ts");
  });

  it("drops partners below the floor and any already in the change set", () => {
    const out = selectMissingCochanges(
      report({
        changed: ["a.ts", "d.ts"],
        cochange_warnings: [
          { changed: "a.ts", missing_partner: "b.ts", score: 2 }, // below floor
          { changed: "a.ts", missing_partner: "d.ts", score: 9 }, // already changed
          { changed: "a.ts", missing_partner: "e.ts", score: 7 }, // kept
        ],
      }),
      4,
    );
    expect(out.map((c) => c.partner)).toEqual(["e.ts"]);
  });

  it("returns nothing when there is no blast payload", () => {
    expect(selectMissingCochanges(report({ blast: null }), 4)).toEqual([]);
  });
});

describe("changeSignature", () => {
  it("is stable for the same set and distinct across sets", () => {
    expect(changeSignature(["a.ts", "b.ts"])).toBe(changeSignature(["a.ts", "b.ts"]));
    expect(changeSignature(["a.ts"])).not.toBe(changeSignature(["a.ts", "b.ts"]));
  });
});
