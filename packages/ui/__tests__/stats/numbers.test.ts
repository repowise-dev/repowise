import { describe, expect, it } from "vitest";
import { ageLabel, gapLabel, momentum, shortCount, wholePct } from "../../src/stats/numbers";

describe("shortCount", () => {
  it("keeps full figures below 100k and shortens above", () => {
    expect(shortCount(13_377)).toBe("13,377");
    expect(shortCount(99_999)).toBe("99,999");
    expect(shortCount(504_211)).toBe("504k");
    expect(shortCount(1_259_713)).toBe("1.26M");
    expect(shortCount(2_000_000)).toBe("2M");
    // Rounds up into millions rather than printing "1000k".
    expect(shortCount(999_700)).toBe("1M");
  });
});

describe("wholePct", () => {
  it("never shows a decimal", () => {
    expect(wholePct(1, 3)).toBe(33);
    expect(wholePct(5, 0)).toBe(0);
  });
});

describe("momentum", () => {
  it("states the counts when either side is small", () => {
    expect(momentum({ recent_90d: 16, prior_90d: 7, pct_change: 128.6 })).toEqual({
      value: "16",
      unit: "commits",
      sub: "up from 7 the 90 days before",
    });
  });

  it("uses a whole percent on a real base", () => {
    expect(momentum({ recent_90d: 2842, prior_90d: 1036, pct_change: 174.3 })?.value).toBe("+174%");
    expect(momentum({ recent_90d: 60, prior_90d: 120, pct_change: -50 })?.value).toBe("-50%");
  });

  it("calls a small change steady", () => {
    expect(momentum({ recent_90d: 105, prior_90d: 100, pct_change: 5 })?.value).toBe("Steady");
  });

  it("drops the comparison when the prior window is cut", () => {
    expect(momentum({ recent_90d: 300, prior_90d: 0, pct_change: null })).toEqual({
      value: "300",
      unit: "commits",
      sub: "in the last 90 days",
    });
  });
});

describe("gapLabel and ageLabel", () => {
  it("switch units at sensible points", () => {
    expect(gapLabel(30)).toEqual({ value: "30", unit: "hours" });
    expect(gapLabel(0.4)).toEqual({ value: "Under", unit: "an hour" });
    expect(gapLabel(1.2)).toEqual({ value: "1", unit: "hour" });
    expect(gapLabel(5.6)).toEqual({ value: "6", unit: "hours" });
    expect(gapLabel(312)).toEqual({ value: "13", unit: "days" });
    expect(ageLabel(235)).toEqual({ value: "235", unit: "days" });
    expect(ageLabel(365)).toEqual({ value: "1", unit: "year" });
    expect(ageLabel(5840)).toEqual({ value: "15", unit: "years" });
  });
});
