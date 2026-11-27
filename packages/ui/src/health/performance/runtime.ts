"use client";

import { useState } from "react";
import useSWR from "swr";
import type {
  BenchmarkRun,
  EvidenceRunSummary,
  EvidenceRunsResponse,
  MeasuredResponse,
  MeasuredRun,
  StatementRun,
  SuiteCostResponse,
} from "@repowise-dev/types/runtime-evidence";

import type { PerformanceViewAdapter } from "./adapter";

/**
 * Runtime evidence reads for the Performance tab, each one shared SWR key per
 * repository. Stored runs change only when someone adds a run, so none of
 * them revalidates on focus.
 */

/** Rows a workload shows before "Show all". */
export const MEASURED_PREVIEW = 5;
/** The most rows a measured request may ask for (the server's cap). */
export const MEASURED_MAX = 100;

const QUIET = { revalidateOnFocus: false, shouldRetryOnError: false } as const;

/**
 * Every workload's newest run and each group's ranked total, with no rows:
 * enough for the status strip, the workload lens and the group headers.
 */
export function useMeasuredOverview(adapter: PerformanceViewAdapter) {
  const load = adapter.getMeasured;
  return useSWR<MeasuredResponse>(
    load ? `evidence-measured:${adapter.cacheKey}:overview` : null,
    () => load!({ limit: 0 }),
    QUIET,
  );
}

/** One run's ranked functions, best rank first; `total` is that run's own. */
export function useMeasuredRows(adapter: PerformanceViewAdapter, runId: string | null, limit: number) {
  const load = adapter.getMeasured;
  return useSWR<MeasuredResponse>(
    load && runId ? `evidence-measured:${adapter.cacheKey}:${runId}:${limit}` : null,
    () => load!({ runId: runId!, limit }),
    { ...QUIET, keepPreviousData: true },
  );
}

/** Every stored run, newest first; read only once the repository has runs. */
export function useEvidenceRuns(adapter: PerformanceViewAdapter, enabled: boolean) {
  const load = adapter.listEvidenceRuns;
  return useSWR<EvidenceRunsResponse>(
    load && enabled ? `evidence-runs:${adapter.cacheKey}` : null,
    () => load!(),
    QUIET,
  );
}

/** Slow tests and costly files listed before "Show 50". */
export const SLOW_TESTS = 10;
/** Rows each tests list shows after "Show 50" (the route allows up to 100). */
export const SLOW_TESTS_MORE = 50;

/**
 * The slowest timed tests and the test time per production file; read once
 * the measured view has answered. Not gated on its status: test timings are
 * stored without any profile run.
 */
export function useEvidenceTests(adapter: PerformanceViewAdapter, enabled: boolean, limit = SLOW_TESTS) {
  const load = adapter.getEvidenceTests;
  return useSWR<SuiteCostResponse>(
    load && enabled ? `evidence-tests:${adapter.cacheKey}:${limit}` : null,
    () => load!({ limit }),
    QUIET,
  );
}

export interface RuntimeEvidenceView {
  overview: MeasuredResponse | undefined;
  /** Runs in a workload class that may lead, newest first. */
  leads: MeasuredRun[];
  /** The workload the strip, the lede split and the table describe. */
  run: MeasuredRun | null;
  pick: (runId: string) => void;
  /** The newest unit-test run, listed apart and never leading. */
  testRun: MeasuredRun | null;
  /** Ranked functions in that run, when it is the only one; else 0. */
  testsTotal: number;
  /** Each workload's newest benchmark run, listed apart: a benchmark never ranks. */
  benchmarkRuns: BenchmarkRun[];
  /**
   * Each production statement workload's newest window that names a function,
   * listed apart: statement rows never lead or reorder the queue.
   */
  statementRuns: StatementRun[];
  /** Every stored run, newest first, once read; empty when the read failed. */
  runs: EvidenceRunSummary[] | undefined;
  /** The commit the index is at, as the runs route names it. */
  referenceCommit: string | null;
  /** Stored test timings: the slowest tests and the test time per file. */
  tests: TestsView | null;
}

export interface TestsView {
  suite: SuiteCostResponse;
  /** Rows each list asked for. */
  limit: number;
  /** Ask for {@link SLOW_TESTS_MORE} rows; null once asked. */
  more: (() => void) | null;
  /** The longer read is in flight. */
  loading: boolean;
  /** The longer read failed; the shorter lists stay. */
  failed: boolean;
}

/**
 * Which workload the tab opens on: production before a load test before an
 * entry point, newest first within a class (the server's order).
 */
const OPENS_ON = ["production", "load_test", "entry_point"];
const opensOn = (runs: MeasuredRun[]) =>
  [...runs].sort(
    (a, b) => OPENS_ON.indexOf(a.workload_class) - OPENS_ON.indexOf(b.workload_class),
  )[0] ?? null;

/** The tab's runtime evidence: what was read and which workload is picked. */
export function useRuntimeEvidenceView(adapter: PerformanceViewAdapter): RuntimeEvidenceView {
  const { data: overview } = useMeasuredOverview(adapter);
  const [picked, setPicked] = useState<string | null>(null);
  const leads = overview?.measured?.runs ?? [];
  const tests = overview?.measured_in_tests;
  // Read once anything is stored: the runs explain an unranked status too.
  const { data: runs, error: runsError } = useEvidenceRuns(
    adapter,
    overview != null && overview.status !== "no_runtime_data",
  );
  // Keyed by repository, so another repository opens on the short lists again.
  const [expanded, setExpanded] = useState<string | null>(null);
  const more = expanded === adapter.cacheKey;
  const testsLimit = more ? SLOW_TESTS_MORE : SLOW_TESTS;
  // The short lists stay on screen while the longer read is in flight; both
  // reads are keyed by repository, so nothing carries over to another one.
  const short = useEvidenceTests(adapter, overview != null, SLOW_TESTS);
  const long = useEvidenceTests(adapter, overview != null && more, SLOW_TESTS_MORE);
  const suite = long.data ?? short.data;
  const suiteError = more ? long.error : null;
  const suiteLoading = more && long.isLoading;
  return {
    overview,
    leads,
    run: leads.find((r) => r.run_id === picked) ?? opensOn(leads),
    pick: setPicked,
    testRun: tests?.runs[0] ?? null,
    testsTotal: tests && tests.runs.length === 1 ? tests.total : 0,
    benchmarkRuns: overview?.benchmarks?.runs ?? [],
    statementRuns: overview?.statements?.runs ?? [],
    // A failed read, or a host that cannot list runs, is no runs, so the
    // notices fall back to the status alone.
    runs: runs?.runs ?? (runsError || !adapter.listEvidenceRuns ? [] : undefined),
    referenceCommit: runs?.reference_commit ?? null,
    tests:
      suite?.status === "measured" && suite.slow_tests.length > 0
        ? {
            suite,
            limit: testsLimit,
            more: testsLimit < SLOW_TESTS_MORE ? () => setExpanded(adapter.cacheKey) : null,
            loading: suiteLoading,
            failed: Boolean(suiteError),
          }
        : null,
  };
}
