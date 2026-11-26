import type {
  EvidenceRunSummary,
  MeasuredRun,
} from "@repowise-dev/types/runtime-evidence";

import { formatNumber, formatPercent } from "../../lib/format";
import { humanizeToken } from "./presentation";

/**
 * What a stored run's own header says about it, beyond what it ranks: where
 * its commit sits against the index, how far it moved from the previous run
 * of the same workload, where its time went, and whether it carries enough
 * signal to rank on. Pure reads of `/evidence/runs` and `/evidence/measured`.
 */

const SECONDS_PER: Record<string, number> = {
  s: 1,
  ms: 1e-3,
  us: 1e-6,
  ns: 1e-9,
};

/** The run's total in seconds, or null when its unit is not a time (samples, tests). */
export function runSeconds(
  run: Pick<EvidenceRunSummary, "total_value" | "unit">,
): number | null {
  const per = run.unit ? SECONDS_PER[run.unit] : undefined;
  return per != null && run.total_value != null ? run.total_value * per : null;
}

/** "982 s", "22.0 s", "0.42 s": enough figures to compare, no more. */
export function secondsText(seconds: number): string {
  const abs = Math.abs(seconds);
  if (abs >= 100) return `${formatNumber(Math.round(seconds))} s`;
  if (abs >= 1) return `${seconds.toFixed(1)} s`;
  if (abs >= 0.01) return `${seconds.toFixed(2)} s`;
  return "under 0.01 s";
}

/** Where a run's commit sits against the indexed one. */
export type CommitPosition =
  "indexed" | "ahead" | "older" | "other" | "unknown";

const ingestedAt = (run: EvidenceRunSummary) =>
  run.ingested_at ? Date.parse(run.ingested_at) : Number.NaN;

/**
 * `ahead` when the run was added after the newest run of the same workload
 * (kind, name, environment) at the indexed commit, `older` when before it,
 * `other` when no such run is at the indexed commit to compare with. Another
 * workload's run never places it, as in the engine's `commit_position`.
 * Ceiling: this reads ingest order, which is commit order for runs a CI job
 * adds on each push; a run of an old commit added late reads as ahead.
 */
export function commitPosition(
  run: EvidenceRunSummary,
  runs: readonly EvidenceRunSummary[],
  reference: string | null,
): CommitPosition {
  if (!run.commit_sha || !reference)
    return run.freshness === "stale" ? "other" : "unknown";
  if (run.commit_sha === reference) return "indexed";
  const anchors = runs
    .filter(
      (r) =>
        r.commit_sha === reference &&
        r.kind === run.kind &&
        r.workload === run.workload &&
        r.environment === run.environment,
    )
    .map(ingestedAt)
    .filter(Number.isFinite);
  const at = ingestedAt(run);
  if (anchors.length === 0 || !Number.isFinite(at)) return "other";
  return at > Math.max(...anchors) ? "ahead" : "older";
}

export const POSITION_TAG: Record<CommitPosition, string | null> = {
  indexed: null,
  ahead: "ahead of index",
  older: "older commit",
  other: "other commit",
  unknown: null,
};

/**
 * Runs that are the same workload measured the same way: name, environment,
 * kind, and the tool, clock and unit, so a switch of profiler starts a new
 * history rather than reading as a change.
 */
const historyKey = (run: EvidenceRunSummary) =>
  [
    run.kind,
    run.workload,
    run.environment,
    run.tool,
    run.value_kind,
    run.unit,
  ].join("\u0000");

/** Points a workload's trend draws, newest last. */
export const TREND_MAX = 20;

export interface RunChange {
  /** The previous run of the same workload. */
  previous: EvidenceRunSummary;
  delta: number;
  /** Change as a fraction of the previous total; null when that was zero. */
  ratio: number | null;
}

export interface RunHistory {
  /** Per run id: the change from the previous run of its workload, when both have a total. */
  changes: Map<string, RunChange>;
  /** Per run id of each workload's newest run: its totals, oldest first, at most {@link TREND_MAX}. */
  trends: Map<string, number[]>;
}

/** Only totals that measure work compare run to run; a test count does not. */
const comparable = (run: EvidenceRunSummary) =>
  run.total_value != null &&
  (run.unit === "samples" || (run.unit != null && run.unit in SECONDS_PER));

/** Each run's change from the one before it, and each workload's trend; `runs` newest first. */
export function runHistory(runs: readonly EvidenceRunSummary[]): RunHistory {
  const groups = new Map<string, EvidenceRunSummary[]>();
  for (const run of runs) {
    if (!comparable(run)) continue;
    const key = historyKey(run);
    groups.set(key, [...(groups.get(key) ?? []), run]);
  }
  const changes = new Map<string, RunChange>();
  const trends = new Map<string, number[]>();
  for (const group of groups.values()) {
    group.forEach((run, i) => {
      const previous = group[i + 1];
      if (!previous) return;
      const delta = run.total_value! - previous.total_value!;
      changes.set(run.run_id, {
        previous,
        delta,
        ratio: previous.total_value ? delta / previous.total_value : null,
      });
    });
    if (group.length > 1) {
      trends.set(
        group[0]!.run_id,
        group
          .slice(0, TREND_MAX)
          .map((r) => r.total_value!)
          .reverse(),
      );
    }
  }
  return { changes, trends };
}

/**
 * Past this change a run is marked slower or faster. Ceiling: wall time on
 * shared CI runners is noisy, so a fixed percent both misses and over-calls;
 * the engine's history-band rule (0.63) replaces it.
 */
export const CHANGE_MARK = 0.1;

/** "+112 s (+12.9%)" or "-1,200 samples (-3.1%)". */
export function changeText(change: RunChange, unit: string | null): string {
  if (change.delta === 0) return "no change";
  const sign = change.delta > 0 ? "+" : change.delta < 0 ? "-" : "";
  const seconds = runSeconds({ total_value: Math.abs(change.delta), unit });
  const amount =
    seconds != null
      ? secondsText(seconds)
      : `${formatNumber(Math.round(Math.abs(change.delta)))} ${unit ?? ""}`.trim();
  const pct =
    change.ratio != null
      ? ` (${sign}${formatPercent(Math.abs(change.ratio), 1)})`
      : "";
  return `${sign}${amount}${pct}`;
}

export function changeMark(change: RunChange): "slower" | "faster" | null {
  if (change.ratio == null || Math.abs(change.ratio) <= CHANGE_MARK)
    return null;
  return change.ratio > 0 ? "slower" : "faster";
}

/** The run's split, with what no indexed function and no wait explains as the rest. */
export interface RunShares {
  repo: number;
  library: number | null;
  wait: number | null;
  /** 1 minus the three, when the run names them; never below zero. */
  unattributed: number | null;
}

/**
 * Null without a repo share. Ceiling: the unattributed part is inferred as
 * the remainder (interpreter start, imports, frames outside indexed code);
 * the engine stamping it on the run header (0.63) replaces the inference.
 */
export function runShares(
  run: Pick<
    EvidenceRunSummary,
    "repo_self_share" | "library_share" | "wait_share"
  >,
): RunShares | null {
  if (run.repo_self_share == null) return null;
  const named =
    run.repo_self_share + (run.library_share ?? 0) + (run.wait_share ?? 0);
  const rest = 1 - named;
  return {
    repo: run.repo_self_share,
    library: run.library_share,
    wait: run.wait_share,
    unattributed:
      run.library_share != null && run.wait_share != null
        ? Math.max(0, rest)
        : null,
  };
}

/** Why a run carries too little signal to rank on, each a cause a better profile fixes. */
export type LowSignalCause =
  | { kind: "pruned"; tool: string }
  | { kind: "no_samples" }
  | { kind: "wait"; share: number }
  | { kind: "few_mapped"; matched: number; total: number | null };

/** Under this many mapped functions a profile ranks too little to steer by. */
export const FEW_MAPPED = 50;
/** ...and under this share of the profiled functions. */
export const FEW_MAPPED_SHARE = 0.75;
/** Over this share of waiting, most of the work ran where the profiler did not look. */
export const WAIT_DOMINATED = 0.5;

/**
 * The causes a profile run reads as low signal for, from its header and, when
 * it ranks, its measured basis. Empty for a run with enough signal, and for a
 * deterministic tracer, which counts calls rather than samples by design.
 * Ceiling: "pruned" is inferred from pyinstrument with no sample counts (its
 * speedscope renderer drops frames under 1 percent); the importer flagging it
 * (0.63) replaces the inference.
 */
export function lowSignalCauses(
  summary: EvidenceRunSummary,
  measured: MeasuredRun | null,
): LowSignalCause[] {
  if (summary.kind !== "profile" || measured?.ordering_only) return [];
  const out: LowSignalCause[] = [];
  if (measured && measured.samples == null) {
    out.push(
      summary.tool?.toLowerCase() === "pyinstrument"
        ? { kind: "pruned", tool: summary.tool }
        : { kind: "no_samples" },
    );
  }
  if (summary.wait_share != null && summary.wait_share > WAIT_DOMINATED) {
    out.push({ kind: "wait", share: summary.wait_share });
  }
  const { matched, total } = summary.units;
  // A small program whose frames nearly all mapped is not low signal.
  if (
    matched != null &&
    matched < FEW_MAPPED &&
    (!total || matched / total < FEW_MAPPED_SHARE)
  ) {
    out.push({ kind: "few_mapped", matched, total });
  }
  return out;
}

/** One cause in words, as the notice and the prompt both say it. */
export function lowSignalText(cause: LowSignalCause): string {
  switch (cause.kind) {
    case "pruned":
      return `no sample counts, and frames under 1% of the run pruned (the ${cause.tool} speedscope export drops them and leaves their time on the caller)`;
    case "no_samples":
      return "no sample counts, so no share has an interval and no function can lead";
    case "wait":
      return `${formatPercent(cause.share)} of its time waiting, on worker processes or I/O the profiler did not sample`;
    case "few_mapped":
      return `only ${formatNumber(cause.matched)}${cause.total != null ? ` of ${formatNumber(cause.total)}` : ""} profiled functions mapped to indexed code`;
  }
}

/** `{outside_repo: 16, vendored: 5}` reads "16 outside repo, 5 vendored"; null when nothing was skipped. */
export function skippedText(
  skipped: Record<string, number> | null,
): string | null {
  const entries = Object.entries(skipped ?? {}).filter(([, n]) => n > 0);
  if (entries.length === 0) return null;
  return entries
    .map(
      ([reason, n]) =>
        `${formatNumber(n)} ${humanizeToken(reason).toLowerCase()}`,
    )
    .join(", ");
}

/** Entries the ingest cut at the row cap, from the units or the skip reasons. */
export function truncatedCount(run: EvidenceRunSummary): number {
  return run.units.truncated ?? run.skipped?.units_truncated ?? 0;
}
