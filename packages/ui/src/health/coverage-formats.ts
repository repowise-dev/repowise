/**
 * Curated display list of the coverage reports users produce, in display
 * order. Not a mirror of the parsers: core's `PARSERS` registry in
 * `analysis/health/coverage/detector.py` is authoritative, and repowise's own
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
