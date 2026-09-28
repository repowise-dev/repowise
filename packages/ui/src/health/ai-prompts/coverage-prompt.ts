import {
  bulletList,
  closingSections,
  explorationCloser,
  FLAVOR_PREAMBLE,
  joinSections,
  repoSuffix,
  type AiPromptFlavor,
} from "./shared";

// ─────────────────────────────────────────────────────────────────────
// Coverage prompt
// ─────────────────────────────────────────────────────────────────────

export interface CoverageFilePromptInput {
  file_path: string;
  line_coverage_pct: number | null;
  branch_coverage_pct?: number | null;
  total_coverable_lines?: number;
  covered_lines?: number[];
  source_format?: string;
  health_score?: number | null;
  nloc?: number | null;
  module?: string | null;
}

export interface BuildCoveragePromptOptions {
  row: CoverageFilePromptInput;
  flavor?: AiPromptFlavor;
  repoName?: string;
}

// Cap to ~20 ranges so the prompt stays readable.
const MAX_UNCOVERED_RANGES = 20;

const CONSTRAINTS = [
  "**Read first, write second.** Read the source file, the existing tests directory, and at least one nearby test file so you adopt the project's conventions instead of inventing your own.",
  "Use the project's existing test framework, fixtures, and naming conventions — don't introduce a new framework.",
  "Cover the listed uncovered branches/lines explicitly; do not just pad coverage with trivial cases.",
  "Each new test must have a clear behavior name (`should …` / `test_*_when_*`), one logical assertion focus, and no shared mutable state with other tests.",
  "Mock external IO (network, filesystem outside fixtures, time, env) — but do not mock the file under test.",
  "If you discover a real bug while writing the tests, add a failing test that documents it and call it out; do not silently fix.",
  "Trust the real source code over the coverage numbers in this prompt. If a line marked uncovered turns out to be unreachable or dead, say so and move on.",
];

const EXPECTED = [
  "1. A short plan: which functions / branches you'll cover and in what order (3–6 bullets).",
  "2. The new tests, in the same test file location convention the project already uses.",
  "3. A coverage estimate: which uncovered ranges your new tests now hit, and which remain.",
  "4. A list of any bugs or surprising behavior you found while writing the tests.",
];

/** Inclusive runs of lines in `1..total` that no test covered. */
function uncoveredSpans(covered: number[], total: number): [number, number][] {
  const set = new Set(covered);
  const spans: [number, number][] = [];
  let start: number | null = null;
  for (let i = 1; i <= total; i++) {
    if (!set.has(i)) {
      if (start === null) start = i;
    } else if (start !== null) {
      spans.push([start, i - 1]);
      start = null;
    }
  }
  if (start !== null) spans.push([start, total]);
  return spans;
}

function uncoveredRanges(
  covered: number[] | undefined,
  total: number | undefined,
): string {
  if (!covered || !total || covered.length === 0) return "";
  const ranges = uncoveredSpans(covered, total);
  if (ranges.length === 0) return "";
  const shown = ranges.slice(0, MAX_UNCOVERED_RANGES);
  const more = ranges.length - shown.length;
  return (
    shown.map(([a, b]) => (a === b ? `${a}` : `${a}–${b}`)).join(", ") +
    (more > 0 ? `, … (+${more} more ranges)` : "")
  );
}

function coverageState(row: CoverageFilePromptInput): string {
  return bulletList([
    row.line_coverage_pct == null
      ? "Line coverage: **no data** — file is not covered by any test run."
      : `Line coverage: **${row.line_coverage_pct.toFixed(1)}%** (lower is worse)`,
    row.branch_coverage_pct == null ? null : `Branch coverage: ${row.branch_coverage_pct.toFixed(1)}%`,
    row.total_coverable_lines ? `Coverable lines: ${row.total_coverable_lines}` : null,
    row.nloc ? `File size: ${row.nloc} NLOC` : null,
    row.health_score != null
      ? `Current health score: ${row.health_score.toFixed(1)}/10 — stronger defect indicators make focused tests especially valuable.`
      : null,
    row.module ? `Module: \`${row.module}\`` : null,
    row.source_format ? `Coverage source: ${row.source_format.toUpperCase()}` : null,
  ]);
}

export function buildCoverageAiPrompt({
  row,
  flavor = "generic",
  repoName,
}: BuildCoveragePromptOptions): string {
  const ranges = uncoveredRanges(row.covered_lines, row.total_coverable_lines);

  return joinSections([
    FLAVOR_PREAMBLE[flavor],
    "",
    `## Target file${repoSuffix(repoName)}`,
    "",
    `\`${row.file_path}\``,
    "",
    "## Current coverage state",
    "",
    coverageState(row),
    "",
    ranges
      ? ["## Uncovered line ranges", "", "```", ranges, "```", ""].join("\n")
      : "",
    "## Your task",
    "",
    bulletList([
      "Add tests that cover the uncovered lines/branches listed above, prioritizing the riskiest code paths.",
      "If the file has no tests at all yet, create the test file in the project's standard location and seed it with the most important happy-path + edge cases first.",
      "Aim for a meaningful coverage jump (≥ 70% line coverage as a target), but quality of assertions matters more than the number.",
    ]),
    "",
    ...closingSections(CONSTRAINTS, EXPECTED),
    explorationCloser(flavor, row.file_path, "coverage"),
  ]);
}
