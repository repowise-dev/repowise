/**
 * Runtime tests locking the health band cutoffs + the pure band mapping.
 *
 * These are the TypeScript half of the cross-language parity guard: the same
 * cutoffs are asserted in core (`tests/unit/health/test_grading.py`). If a
 * silent retune changes one side without the other, one of the two snapshots
 * fails CI. The 3 buckets are defect-backed (Alert ~17x the defect rate of
 * Healthy) so the boundaries are intentionally frozen.
 */

import { describe, expect, it } from "vitest";
import { ALERT_MAX, HEALTHY_MIN, bandForScore } from "../src/health.js";

describe("health band cutoffs", () => {
  it("are the frozen defect-backed values", () => {
    expect(ALERT_MAX).toBe(4.0);
    expect(HEALTHY_MIN).toBe(8.0);
  });
});

describe("bandForScore", () => {
  it("maps each band including boundaries", () => {
    expect(bandForScore(1.0)).toBe("alert");
    expect(bandForScore(3.99)).toBe("alert");
    expect(bandForScore(4.0)).toBe("warning"); // ALERT_MAX is inclusive of warning
    expect(bandForScore(6.0)).toBe("warning");
    expect(bandForScore(7.99)).toBe("warning");
    expect(bandForScore(8.0)).toBe("healthy"); // HEALTHY_MIN is inclusive of healthy
    expect(bandForScore(10.0)).toBe("healthy");
  });
});
