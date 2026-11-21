"use client";

import { useState } from "react";
import useSWR from "swr";
import type {
  EvidenceRunSummary,
  EvidenceRunsResponse,
  MeasuredResponse,
  MeasuredRun,
  SlowTest,
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

/** Slow tests listed before the list folds. */
export const SLOW_TESTS = 10;

/**
 * The slowest timed tests; read once the measured view has answered. Not
 * gated on its status: test timings are stored without any profile run.
 */
export function useEvidenceTests(adapter: PerformanceViewAdapter, enabled: boolean) {
  const load = adapter.getEvidenceTests;
  return useSWR<SuiteCostResponse>(
    load && enabled ? `evidence-tests:${adapter.cacheKey}` : null,
    () => load!({ limit: SLOW_TESTS }),
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
  /** Every stored run, once read. */
  runs: EvidenceRunSummary[] | undefined;
  /** The slowest timed tests, longest first, and how many tests were timed. */
  slowTests: { tests: SlowTest[]; total: number } | null;
}

/**
 * Which workload the tab opens on: real work before a benchmark of a piece
 * of it, newest first within a class (the server's order).
 */
const OPENS_ON = ["production", "load_test", "entry_point", "benchmark"];
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
  const { data: runs } = useEvidenceRuns(
    adapter,
    overview != null && overview.status !== "no_runtime_data",
  );
  const { data: suite } = useEvidenceTests(adapter, overview != null);
  return {
    overview,
    leads,
    run: leads.find((r) => r.run_id === picked) ?? opensOn(leads),
    pick: setPicked,
    testRun: tests?.runs[0] ?? null,
    testsTotal: tests && tests.runs.length === 1 ? tests.total : 0,
    runs: runs?.runs,
    slowTests:
      suite?.status === "measured" && suite.slow_tests.length > 0
        ? { tests: suite.slow_tests, total: suite.tests_total }
        : null,
  };
}
