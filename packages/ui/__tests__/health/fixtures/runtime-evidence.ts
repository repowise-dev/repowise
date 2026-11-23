import { vi } from "vitest";
import type {
  EvidenceRunSummary,
  EvidenceRunsResponse,
  MeasuredResponse,
  MeasuredRow,
  MeasuredRun,
} from "@repowise-dev/types/runtime-evidence";

/** Runtime evidence shaped exactly like the `/evidence/*` routes return it. */

export function measuredRun(overrides: Partial<MeasuredRun> = {}): MeasuredRun {
  return {
    run_id: "run-cli",
    workload_class: "entry_point",
    workload_name: "cli build",
    commit: "4f9c2a1e0b",
    measured_at: new Date(Date.now() - 2 * 86_400_000).toISOString(),
    age_days: 2,
    samples: 5312,
    repo_self_share: 0.114,
    wait_share: 0.344,
    library_share: 0.542,
    outside_production: 41,
    value_kind: "cpu",
    ordering_only: false,
    ...overrides,
  };
}

export function measuredRow(overrides: Partial<MeasuredRow> = {}): MeasuredRow {
  return {
    symbol_id: "src/render/markdown.py::render_block",
    file_path: "src/render/markdown.py",
    lines: [88, 131],
    run_id: "run-cli",
    rank: 1,
    share: 0.029,
    share_low: 0.025,
    share_high: 0.034,
    inclusive: 0.068,
    samples: 154,
    freshness: "current",
    callers_total: 4,
    callers: ["src/site.py::render_all"],
    owner: { name: "dana", commit_pct: 0.62 },
    tests: ["tests/test_markdown.py::test_render"],
    on_static_findings: ["perf3_render"],
    ...overrides,
  };
}

const CLI_ROWS: MeasuredRow[] = [
  measuredRow(),
  measuredRow({
    symbol_id: "src/content/loader.py::read_frontmatter",
    file_path: "src/content/loader.py",
    lines: [41, 60],
    rank: 2,
    share: 0.021,
    share_low: 0.018,
    share_high: 0.025,
    samples: 112,
    owner: null,
    tests: null,
    on_static_findings: [],
  }),
  ...Array.from({ length: 5 }, (_, i) =>
    measuredRow({
      symbol_id: `src/util/u${i}.py::helper_${i}`,
      file_path: `src/util/u${i}.py`,
      rank: i + 3,
      share: 0.01 - i * 0.001,
      on_static_findings: [],
    }),
  ),
];

const BENCH = measuredRun({
  run_id: "run-bench",
  workload_class: "benchmark",
  workload_name: "bench parse",
  samples: 8904,
  repo_self_share: 0.02,
});
const BENCH_ROWS = [
  measuredRow({ run_id: "run-bench", symbol_id: "src/parse.py::tokenize", file_path: "src/parse.py" }),
];
const TESTS = measuredRun({
  run_id: "run-tests",
  workload_class: "unit_test",
  workload_name: "pytest tests/",
  samples: 21450,
});
const TEST_ROWS = [
  measuredRow({ run_id: "run-tests", symbol_id: "src/slug.py::slugify", file_path: "src/slug.py" }),
];

const ROWS: Record<string, [MeasuredRun, MeasuredRow[]]> = {
  "run-cli": [measuredRun(), CLI_ROWS],
  "run-bench": [BENCH, BENCH_ROWS],
  "run-tests": [TESTS, TEST_ROWS],
};

/** The `/evidence/measured` route over the runs above: `limit` rows per run. */
export async function measured(opts: { runId?: string; limit?: number } = {}): Promise<MeasuredResponse> {
  const limit = opts.limit ?? 10;
  const group = (ids: string[]) => {
    const kept = ids.filter((id) => !opts.runId || id === opts.runId);
    if (kept.length === 0) return null;
    return {
      runs: kept.map((id) => ROWS[id]![0]),
      rows: kept.flatMap((id) => ROWS[id]![1].slice(0, limit)),
      total: kept.reduce((n, id) => n + ROWS[id]![1].length, 0),
    };
  };
  return {
    basis: "measured",
    status: "measured",
    command: null,
    measured: group(["run-cli", "run-bench"]),
    measured_in_tests: group(["run-tests"]),
  };
}

export const NOTHING_MEASURED: MeasuredResponse = {
  basis: "unknown",
  status: "no_runtime_data",
  command: null,
  measured: null,
  measured_in_tests: null,
};

export function runSummary(overrides: Partial<EvidenceRunSummary> = {}): EvidenceRunSummary {
  return {
    run_id: "run-cli",
    kind: "profile",
    workload: "cli build",
    class: "entry_point",
    environment: "local",
    value_kind: "cpu",
    unit: "samples",
    tool: "pyinstrument",
    tool_version: "4.7",
    commit_sha: "4f9c2a1e0b",
    freshness: "current",
    dirty: false,
    ingested_at: new Date(Date.now() - 2 * 86_400_000).toISOString(),
    units: { total: 100, matched: 96, skipped: 4, outside_repo: 0, truncated: 0 },
    skipped: null,
    total_value: 5312,
    repo_self_share: 0.114,
    wait_share: 0.344,
    library_share: 0.542,
    mapping_partial: false,
    tests_failed: null,
    ...overrides,
  };
}

export const RUNS: EvidenceRunsResponse = {
  reference_commit: "4f9c2a1e0b",
  runs: [
    runSummary(),
    runSummary({ run_id: "run-bench", workload: "bench parse", class: "benchmark" }),
    runSummary({
      run_id: "run-junit",
      kind: "test_timing",
      workload: "pytest tests/",
      class: "unit_test",
      tool: "JUnit XML",
      tool_version: null,
      commit_sha: "b81d0e7aa",
      freshness: "stale",
      total_value: 1240,
      unit: "tests",
      tests_failed: 2,
    }),
  ],
};

/** Adapter members for a repository with runtime evidence stored. */
export function evidenceAdapter() {
  return {
    getMeasured: vi.fn(measured),
    listEvidenceRuns: vi.fn(async () => RUNS),
  };
}
