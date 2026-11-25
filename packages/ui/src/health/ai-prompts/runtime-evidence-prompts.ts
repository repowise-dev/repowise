import type {
  CostCluster,
  SlowTest,
} from "@repowise-dev/types/runtime-evidence";

import {
  lowSignalText,
  secondsText,
  type LowSignalCause,
} from "../performance/run-signal";
import {
  bulletList,
  closingSections,
  joinSections,
  preamble,
  REPO_ROLE,
  repoSuffix,
  type AiPromptFlavor,
} from "./shared";

// ─────────────────────────────────────────────────────────────────────
// A profile with too little signal: collect one that ranks
// ─────────────────────────────────────────────────────────────────────

export interface BuildRichProfilePromptOptions {
  /** The workload the weak run profiled, as stored. */
  workload: string;
  /** The profiler that wrote it, when the run names one. */
  tool: string | null;
  causes: readonly LowSignalCause[];
  flavor?: AiPromptFlavor;
}

/** What fixes each cause, said once per kind. */
const FIX: Record<LowSignalCause["kind"], string> = {
  pruned:
    "Do not export with `pyinstrument -r speedscope`: it drops every frame under 1% of the run. Use the sampled recipe `repowise evidence setup` prints for this workload, which keeps every frame and its sample count.",
  no_samples:
    "Use a sampling profiler that records sample counts (py-spy, or the recipe `repowise evidence setup` prints), not an evented export.",
  wait: "Most of the run waits on work the profiler did not sample. If the program fans out to worker processes, profile them too: `py-spy record --subprocesses --format speedscope -o <file> -- <command>`.",
  few_mapped:
    "Profile from the repository root at the indexed commit, with no uncommitted changes, so frames map to indexed functions, and run a workload long enough to reach most of the code.",
};

/** Said once when a fix names py-spy: its file does not say which clock it sampled, and ingest refuses it without one. */
const PY_SPY_CLOCK =
  "A py-spy profile does not say which clock it sampled: add it with `--value-kind cpu` (`value_kind=cpu` on a CI upload), or it is refused.";

/**
 * Re-profile a workload whose stored run ranks too little. Names each cause
 * the run showed and its fix, and leaves the ingest step to wherever the
 * last run was added (a CI job or a local `repowise evidence add`).
 */
export function buildRichProfilePrompt({
  workload,
  tool,
  causes,
  flavor = "generic",
}: BuildRichProfilePromptOptions): string {
  const fixes = Array.from(new Set(causes.map((c) => FIX[c.kind])));
  if (fixes.some((fix) => fix.includes("py-spy"))) fixes.push(PY_SPY_CLOCK);
  return joinSections([
    `${REPO_ROLE[flavor]} Repowise stores a runtime profile of \`${workload}\`${tool ? ` (${tool})` : ""}, but the run carries too little signal to rank functions by measured time.`,
    "",
    "## What the stored run shows",
    "",
    bulletList(causes.map(lowSignalText)),
    "",
    "## What to change",
    "",
    bulletList(fixes),
    "",
    `Change the profiling step where \`${workload}\` is profiled today (a CI workflow or a local script), keep the workload name, run it once, and add the new profile the same way the last one was added. Then run \`repowise evidence status\` and report its answer.`,
    "",
    ...closingSections(
      [
        "Do not change the program being profiled, only how it is profiled.",
        "Keep the profile file under the evidence size limit; lower the sampling rate if it is not.",
      ],
      [
        "1. The profiling command you changed, before and after.",
        "2. What `repowise evidence status` reports for the new run.",
      ],
    ),
  ]);
}

// ─────────────────────────────────────────────────────────────────────
// Slow tests, or the tests that run one costly file
// ─────────────────────────────────────────────────────────────────────

const TEST_PARTS = {
  source:
    "Repowise read the durations below from stored JUnit timings of the test suite. A duration is wall time on the machine that ran the suite.",
  open: "each test, its fixtures and the code it runs",
  mcpTail:
    "For each test file, call `get_context([file])` for what it covers, then read the fixtures it uses.",
};

const TEST_CONSTRAINTS = [
  "Keep every assertion and what it covers; a faster test that checks less is not the goal.",
  "Look first at setup: fixtures rebuilt per test, real I/O or sleeps, network calls, and data larger than the assertion needs.",
  "One change per test or fixture, each independently revertible.",
  "Run the changed tests before and after, and report both durations.",
];

const TEST_EXPECTED = [
  "1. Where each test's time goes, and whether it is worth changing.",
  "2. For each change: the edit, the durations before and after, and why coverage is unchanged.",
];

export type SlowTestsSubject =
  | { kind: "tests"; tests: readonly SlowTest[] }
  | { kind: "cluster"; cluster: CostCluster };

export interface BuildSlowTestsPromptOptions {
  subject: SlowTestsSubject;
  flavor?: AiPromptFlavor;
  repoName?: string;
}

function testLine(t: SlowTest, i: number): string {
  const where = t.test_file ? ` in \`${t.test_file}\`` : "";
  const outcome = t.outcome && t.outcome !== "passed" ? `, ${t.outcome}` : "";
  return `${i + 1}. \`${t.test_id}\`${where}: ${secondsText(t.duration_s)}${outcome} (workload \`${t.workload}\`)`;
}

function clusterLines(c: CostCluster, repoName?: string): string[] {
  const more = c.files_total - c.files.length;
  const files = `${c.files.map((f) => `\`${f}\``).join(", ")}${more > 0 ? ` and ${more} more` : ""}`;
  const fx = c.fixture;
  const others = fx
    ? fx.others === 0
      ? "no other timed test does"
      : `${fx.others} other timed test${fx.others === 1 ? "" : "s"} do`
    : "";
  return [
    `## ${c.tests} tests that run the same code and take longer than the rest${repoSuffix(repoName)}`,
    "",
    bulletList([
      `They take ${secondsText(c.mean_s)} each against ${secondsText(c.others_mean_s)} for the other tests: ${secondsText(c.excess_s)} over that pace${c.share != null ? `, ${Math.round(c.share * 100)}% of the suite` : ""}.`,
      `The production code exactly these tests run, from the per-test coverage map: ${files}.`,
      c.within &&
        `They are part of the ${c.within.tests} tests that run \`${c.within.files[0]}\`.`,
      fx &&
        `Start with fixture \`${fx.name}\` (\`${fx.file_path}${fx.line != null ? `:${fx.line}` : ""}\`): ${fx.tests} of them ask for it and ${others}. Check what it builds per test and whether a wider scope or a lighter setup keeps every assertion.`,
      `Slowest of them: ${c.slowest.map((t) => `\`${t.test_id}\` ${secondsText(t.duration_s)}`).join(", ")}.`,
    ]),
  ];
}

/** Make slow tests faster, or a group of tests that share slow setup, without losing coverage. */
export function buildSlowTestsPrompt({
  subject,
  flavor = "generic",
  repoName,
}: BuildSlowTestsPromptOptions): string {
  const body =
    subject.kind === "tests"
      ? [
          `## Slow tests${repoSuffix(repoName)}`,
          "",
          subject.tests.map(testLine).join("\n"),
        ]
      : clusterLines(subject.cluster, repoName);
  return joinSections([
    preamble(flavor, TEST_PARTS),
    "",
    ...body,
    "",
    ...closingSections(TEST_CONSTRAINTS, TEST_EXPECTED),
  ]);
}
