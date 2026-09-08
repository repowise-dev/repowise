/**
 * Runtime tests locking the health band cutoffs + the pure band mapping.
 *
 * These are the TypeScript half of the cross-language parity guard: the same
 * cutoffs are asserted in core (`tests/unit/health/test_grading.py`). If a
 * silent retune changes one side without the other, one of the two snapshots
 * fails CI. The bands are absolute, so a score means the same thing behind a
 * firewall as it does against a public corpus.
 */

import { describe, expect, it } from "vitest";
import {
  EXCELLENT_MIN,
  FAIR_MIN,
  GOOD_MIN,
  HEALTH_BAND_LABEL,
  HEALTH_BAND_ORDER,
  HEALTH_BAND_RANGE_LABEL,
  HEALTH_DIMENSIONS,
  NEEDS_WORK_MIN,
  PERF_BOUNDARY_LABEL,
  bandForScore,
} from "../src/health.js";
import { C4_IO_KINDS } from "../src/external-systems.js";

describe("health band cutoffs", () => {
  it("are the frozen values core mirrors", () => {
    expect(EXCELLENT_MIN).toBe(8.5);
    expect(GOOD_MIN).toBe(7.0);
    expect(FAIR_MIN).toBe(5.5);
    expect(NEEDS_WORK_MIN).toBe(4.0);
  });

  it("give every band a label and a range, worst-first", () => {
    expect(HEALTH_BAND_ORDER).toEqual(["at_risk", "needs_work", "fair", "good", "excellent"]);
    for (const band of HEALTH_BAND_ORDER) {
      expect(HEALTH_BAND_LABEL[band]).toBeTruthy();
      expect(HEALTH_BAND_RANGE_LABEL[band]).toBeTruthy();
    }
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
    expect(bandForScore(10.0)).toBe("excellent");
    expect(bandForScore(8.5)).toBe("excellent"); // each cutoff belongs to the band above
    expect(bandForScore(8.49)).toBe("good");
    expect(bandForScore(7.0)).toBe("good");
    expect(bandForScore(6.99)).toBe("fair");
    expect(bandForScore(5.5)).toBe("fair");
    expect(bandForScore(5.49)).toBe("needs_work");
    expect(bandForScore(4.0)).toBe("needs_work");
    expect(bandForScore(3.99)).toBe("at_risk");
    expect(bandForScore(1.0)).toBe("at_risk");
  });
});
