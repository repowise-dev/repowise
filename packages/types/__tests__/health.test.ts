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
import {
  ALERT_MAX,
  HEALTHY_MIN,
  HEALTH_DIMENSIONS,
  PERF_BOUNDARY_LABEL,
  bandForScore,
} from "../src/health.js";
import { C4_IO_KINDS } from "../src/external-systems.js";

describe("health band cutoffs", () => {
  it("are the frozen defect-backed values", () => {
    expect(ALERT_MAX).toBe(4.0);
    expect(HEALTHY_MIN).toBe(8.0);
  });
});

describe("health dimensions", () => {
  it("match core's DIMENSIONS order (parity guard)", () => {
    // Mirror of `DIMENSIONS` in
    // packages/core/src/repowise/core/analysis/health/scoring.py. The Python
    // half of this guard lives in tests/unit/health/test_scoring_dimensions.py.
    expect(HEALTH_DIMENSIONS).toEqual(["defect", "maintainability", "performance"]);
  });

  it("labels exactly the canonical I/O-boundary kinds (perf finding detail)", () => {
    // PERF_BOUNDARY_LABEL keys must cover the canonical io_kind set and nothing
    // else, so every `boundary_kind` a perf finding can carry renders a label.
    // C4_IO_KINDS is itself parity-locked to the Python IO_KINDS classifier in
    // __tests__/contracts.test.ts + tests/unit/ingestion/test_io_kind.py.
    expect(Object.keys(PERF_BOUNDARY_LABEL).sort()).toEqual([...C4_IO_KINDS].sort());
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
