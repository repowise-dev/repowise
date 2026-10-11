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
import { CATEGORY_CAP } from "../../src/health/biomarker-glossary.js";

describe("CATEGORY_CAP parity", () => {
  it("pins the performance cap to scoring.py's _PERFORMANCE_CATEGORY_CAPS", () => {
    expect(CATEGORY_CAP.performance).toBe(2.0);
  });
});
