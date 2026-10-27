/**
 * Parity guard for the glossary's fallback category caps.
 *
 * `CATEGORY_CAP` is the TypeScript mirror of the category-cap tables in
 * `packages/core/src/repowise/core/analysis/health/scoring.py`. The rendered
 * cap always prefers the server-supplied value (see score-breakdown.tsx), so
 * this constant only backstops older payloads — but a silent retune of one
 * side without the other should still fail CI, the same way the band cutoffs
 * are pinned in `packages/types/__tests__/health.test.ts` +
 * `tests/unit/health/test_grading.py`. The Python half of this pin is the
 * cap-bound behavior in `tests/unit/health/test_scoring_dimensions.py`
 * (`test_perf_category_cap_bounds_dimension`).
 */

import { describe, expect, it } from "vitest";
import {
  CATEGORY_CAP,
  PERFORMANCE_HOME_BIOMARKERS,
  biomarkerLabel,
  isKnownBiomarker,
} from "../../src/health/biomarker-glossary.js";

describe("CATEGORY_CAP parity", () => {
  it("pins the performance cap to scoring.py's _PERFORMANCE_CATEGORY_CAPS", () => {
    expect(CATEGORY_CAP.performance).toBe(2.0);
  });
});

describe("isKnownBiomarker", () => {
  it("knows current ids and a renamed marker's old id", () => {
    expect(isKnownBiomarker("repeated_nesting")).toBe(true);
    expect(isKnownBiomarker("bumpy_road")).toBe(true);
    expect(biomarkerLabel("bumpy_road")).toBe(biomarkerLabel("repeated_nesting"));
  });

  it("rejects ids the glossary does not describe", () => {
    expect(isKnownBiomarker("not_a_marker")).toBe(false);
    expect(isKnownBiomarker("constructor")).toBe(false);
  });
});

describe("performance glossary", () => {
  it("describes every performance biomarker", () => {
    const missing = [...PERFORMANCE_HOME_BIOMARKERS].filter((name) => !isKnownBiomarker(name));
    expect(missing).toEqual([]);
  });
});
