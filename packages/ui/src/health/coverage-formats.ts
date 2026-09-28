/**
 * Coverage report formats `repowise coverage add` reads, in display order.
 * Mirrors the parsers in core's `analysis/health/coverage/`; repowise's own
 * JSON is left out because nobody hand-produces it.
 */
export const COVERAGE_REPORT_FORMATS = [
  "LCOV",
  "Cobertura",
  "Clover",
  "Go coverprofile",
  "JaCoCo",
  "coverage.py",
] as const;

/** The micro-label form shown under the empty-state commands. */
export const COVERAGE_REPORT_FORMATS_LABEL = COVERAGE_REPORT_FORMATS.join(" · ");
