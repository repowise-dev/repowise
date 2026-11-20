"use client";

import useSWR from "swr";
import type { PerformanceOpportunity } from "@repowise-dev/types/health";
import type { MeasuredFunction } from "@repowise-dev/types/runtime-evidence";

import { formatNumber, formatRelativeTimeOrNull } from "../../../lib/format";
import type { PerformanceViewAdapter } from "../adapter";
import { useMeasuredOverview } from "../runtime";
import { EvidenceWordTag } from "../tags";
import { intervalText, plural, shareText, workloadClassLabel } from "../measured-format";
import { Section } from "./layout";

/**
 * What stored profile runs measured on the function this cause edits, from
 * its basis run. Named "runs" because the drawer's evidence section is the
 * static observations. Renders nothing when no run measured the function:
 * no row means unknown, never cheap.
 */

const FRESHNESS: Record<NonNullable<MeasuredFunction["freshness"]>, string> = {
  current: "current",
  body_unchanged: "body unchanged",
  stale: "stale",
};

function basisLine(fn: MeasuredFunction): string {
  const parts: string[] = [];
  if (fn.samples != null) {
    parts.push(
      fn.run_samples != null
        ? `${formatNumber(fn.samples)} of ${formatNumber(fn.run_samples)} samples`
        : plural(fn.samples, "sample"),
    );
  }
  parts.push("self time");
  const commit = fn.evidence_commit_sha?.slice(0, 7);
  if (commit) parts.push(commit);
  if (fn.freshness) parts.push(FRESHNESS[fn.freshness]);
  const age = formatRelativeTimeOrNull(fn.measured_at, "");
  if (age) parts.push(age);
  return parts.join(" · ");
}

function RunLine({ fn }: { fn: MeasuredFunction }) {
  const interval = intervalText(fn.share_low, fn.share_high);
  const cost =
    fn.ordering_only || fn.self_share == null
      ? fn.rank != null
        ? `rank ${formatNumber(fn.rank)}`
        : "measured"
      : shareText(fn.self_share);
  return (
    <div className="grid grid-cols-[minmax(0,1fr)_auto] gap-x-3 gap-y-0.5 border-y border-[var(--color-border-default)] py-2.5 text-xs">
      <span className="font-mono text-[var(--color-text-primary)] [overflow-wrap:anywhere]">
        {fn.workload_name ?? "a stored run"}
        {fn.workload_class ? ` · ${workloadClassLabel(fn.workload_class)}` : ""}
      </span>
      <span className="text-right font-mono tabular-nums text-[var(--color-text-primary)]">
        {cost}
        {interval && !fn.ordering_only ? (
          <span className="ml-1.5 text-[var(--color-text-tertiary)]">{interval}</span>
        ) : null}
      </span>
      <span className="col-span-2 font-mono text-[11px] text-[var(--color-text-tertiary)]">
        {basisLine(fn)}
        {fn.freshness === "stale" ? (
          <span className="ml-1.5">
            <EvidenceWordTag>changed since</EvidenceWordTag>
          </span>
        ) : null}
      </span>
    </div>
  );
}

export function MeasuredRunsSection({
  current,
  adapter,
}: {
  current: PerformanceOpportunity;
  adapter: PerformanceViewAdapter;
}) {
  const load = adapter.getMeasuredFunctions;
  const symbolId = current.intervention_symbol;
  // The tab's own overview read, shared by key: a repository with no stored
  // runs never asks about one function.
  const { data: overview } = useMeasuredOverview(adapter);
  const stored = overview != null && overview.status !== "no_runtime_data";
  const { data } = useSWR(
    load && symbolId && stored ? `evidence-function:${adapter.cacheKey}:${symbolId}` : null,
    () => load!({ symbolId: symbolId! }),
    { revalidateOnFocus: false, shouldRetryOnError: false },
  );
  const fn = data?.functions[0];
  if (!fn) return null;
  const others = (fn.run_count ?? 1) - 1;
  const ranked = fn.rank != null && !fn.ordering_only;
  const notes = [
    fn.total_share != null && fn.workload_name
      ? `Inclusive share in ${fn.workload_name}: ${shareText(fn.total_share)}, for context.`
      : null,
    ranked ? "The rank is by self time." : "The run orders functions within itself only.",
    others > 0
      ? `${plural(others, "other run")} also measured it; this is the run its figures come from.`
      : null,
  ].filter(Boolean);
  return (
    <Section title="Runs that measured it">
      <RunLine fn={fn} />
      {notes.length > 0 ? (
        <p className="mt-2 text-xs text-[var(--color-text-tertiary)]">{notes.join(" ")}</p>
      ) : null}
    </Section>
  );
}
