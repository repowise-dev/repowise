import { vi } from "vitest";
import type {
  BenchmarkRow,
  BenchmarkRun,
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
    owner: { name: "dana", line_pct: 0.62 },
    last_change: null,
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

const SERVE = measuredRun({
  run_id: "run-serve",
  workload_class: "entry_point",
  workload_name: "cli serve",
  samples: 8904,
  repo_self_share: 0.02,
});
const SERVE_ROWS = [
  measuredRow({ run_id: "run-serve", symbol_id: "src/parse.py::tokenize", file_path: "src/parse.py" }),
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
  "run-serve": [SERVE, SERVE_ROWS],
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
    measured: group(["run-cli", "run-serve"]),
    measured_in_tests: group(["run-tests"]),
  };
}

export const BENCH_RUN: BenchmarkRun = {
  run_id: "run-gobench",
  workload_class: "benchmark",
  workload_name: "go test -bench",
  commit: "4f9c2a1e0b",
  measured_at: new Date(Date.now() - 86_400_000).toISOString(),
  age_days: 1,
};

/** Rows naming a function come first, as the route orders them. */
const BENCH_ROWS: BenchmarkRow[] = [
  {
    run_id: "run-gobench",
    benchmark: "BenchmarkParse",
    benchmark_symbol_id: "parse/parse_test.go::BenchmarkParse",
    measures: "parse/parse.go::Parse",
    time: 1234.5,
    unit: "ns",
    allocs_per_op: 12,
    bytes_per_op: 2048,
  },
  {
    run_id: "run-gobench",
    benchmark: "BenchmarkMisc/small",
    benchmark_symbol_id: null,
    measures: null,
    time: 88,
    unit: "ns",
    allocs_per_op: 0,
    bytes_per_op: null,
  },
];

/**
 * The route with benchmark results stored: `limit` rows of the benchmark run,
 * whose own run id reads only it. `base` is what the route says about ranked
 * runs (null for a repository with benchmark results only).
 */
export function withBenchmarks(base: typeof measured | null) {
  return async (opts: { runId?: string; limit?: number } = {}): Promise<MeasuredResponse> => {
    const benchmarks = {
      runs: [BENCH_RUN],
      rows: BENCH_ROWS.slice(0, opts.limit ?? 10),
      total: BENCH_ROWS.length,
    };
    if (opts.runId === BENCH_RUN.run_id || !base) {
      return { basis: "measured", status: "measured", command: null, measured: null, measured_in_tests: null, benchmarks };
    }
    const view = await base(opts);
    return opts.runId ? view : { ...view, benchmarks };
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
    runSummary({ run_id: "run-serve", workload: "cli serve", class: "entry_point" }),
    runSummary({
      run_id: "run-junit",
      kind: "test_timing",
      workload: "pytest tests/",
      class: "unit_test",
      tool: "JUnit XML",
      tool_version: null,
      commit_sha: "b81d0e7aa",
      freshness: "stale",
      // Added before the runs at the indexed commit: an older commit.
      ingested_at: new Date(Date.now() - 3 * 86_400_000).toISOString(),
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
