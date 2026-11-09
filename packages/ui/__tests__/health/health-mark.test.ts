import { describe, expect, it } from "vitest";
import { bandForScore } from "@repowise-dev/types/health";
import { healthBand, healthMark, scoreTextColor } from "../../src/health/tokens";

describe("healthMark", () => {
  it("reads a score on the five canonical bands, edges inclusive", () => {
    expect(healthMark(8.5)).toMatchObject({ band: "excellent", label: "Excellent" });
    expect(healthMark(7)).toMatchObject({ band: "good", label: "Good" });
    expect(healthMark(6.99)).toMatchObject({ band: "fair", label: "Fair" });
    expect(healthMark(4)).toMatchObject({ band: "needs_work", label: "Needs work" });
    expect(healthMark(3.99)).toMatchObject({ band: "at_risk", label: "At risk" });
  });

  it("reads 7.7 as Good in green, the same as every other health surface", () => {
    const mark = healthMark(7.7);
    expect(mark).toMatchObject({ value: "7.7", band: "good", label: "Good", color: "var(--color-success)" });
    expect(mark.band).toBe(bandForScore(7.7));
    expect(healthBand(7.7)).toEqual({ label: mark.label, color: mark.color });
    expect(scoreTextColor(7.7)).toBe("text-[var(--color-success)]");
  });

  it("floors the figure so it never prints across a band edge", () => {
    expect(healthMark(6.96)).toMatchObject({ value: "6.9", label: "Fair" });
    expect(healthMark(8.5)).toMatchObject({ value: "8.5", label: "Excellent" });
  });

  it("paints each band in its semantic colour", () => {
    expect(healthMark(9).color).toBe("var(--color-success)");
    expect(healthMark(6).color).toBe("var(--color-caution)");
    expect(healthMark(5).color).toBe("var(--color-warning)");
    expect(healthMark(2).color).toBe("var(--color-error)");
  });
});
