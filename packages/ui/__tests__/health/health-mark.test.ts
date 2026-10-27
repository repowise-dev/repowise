import { describe, expect, it } from "vitest";
import { HEALTHY_MIN, NEEDS_WORK_MIN } from "@repowise-dev/types/health";
import { HEALTH_MARK_BANDS, healthMark } from "../../src/health/tokens";

describe("healthMark", () => {
  it("reads a score on the three bands, edges inclusive", () => {
    expect(healthMark(HEALTHY_MIN)).toMatchObject({ band: "healthy", label: "Healthy" });
    expect(healthMark(7.99)).toMatchObject({ band: "warning", label: "Warning" });
    expect(healthMark(NEEDS_WORK_MIN)).toMatchObject({ band: "warning" });
    expect(healthMark(3.99)).toMatchObject({ band: "alert", label: "Alert" });
    expect(healthMark(1)).toMatchObject({ band: "alert" });
  });

  it("floors the figure so it never prints across a band edge", () => {
    expect(healthMark(7.96)).toMatchObject({ value: "7.9", label: "Warning" });
    expect(healthMark(8.5)).toMatchObject({ value: "8.5", label: "Healthy" });
  });

  it("paints each band in its own semantic colour", () => {
    expect(healthMark(9).color).toBe("var(--color-success)");
    expect(healthMark(6).color).toBe("var(--color-warning)");
    expect(healthMark(2).color).toBe("var(--color-error)");
  });

  it("keys the bands on the shared cutoffs, top to bottom", () => {
    expect(HEALTH_MARK_BANDS.map((b) => b.min)).toEqual([HEALTHY_MIN, NEEDS_WORK_MIN, 0]);
  });
});
