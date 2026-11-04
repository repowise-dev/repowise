"use client";

import type { PerformanceOpportunity } from "@repowise-dev/types/health";
import { biomarkerLabel } from "../biomarker-glossary";
import { ACTIONABILITY_LABEL } from "../performance/presentation";
import { MICRO_LABEL } from "./shared";
import { formatNumber } from "../../lib/format";

/**
 * The file's open performance causes, when the reader arrived from that lens.
 *
 * Counts and causes, not a score: the performance pillar compresses into a
 * narrow band and a number from it says nothing about this file.
 *
 * A cause is titled by the shape the detector recognized and identified by
 * where it fires, because on one file that is what tells two of them apart:
 * this repository has files carrying thirty-six causes of a single marker. The
 * section says plainly when the host never asked for the data, because an
 * empty list would read as a clear file.
 */
export function PerformanceCauses({
  page,
  loading,
  onSelect,
}: {
  page: { items: PerformanceOpportunity[]; total: number } | null;
  loading: boolean;
  onSelect?: ((opportunityId: string) => void) | undefined;
}) {
  const items = page?.items ?? [];
  const withPlan = items.filter((o) => o.plan_id).length;
  return (
    <section className="flex flex-col gap-2">
      <h3 className={MICRO_LABEL}>Performance causes</h3>
      {loading ? (
        <p className="text-xs text-[var(--color-text-tertiary)]">Loading causes…</p>
      ) : page === null ? (
        <p className="text-xs text-[var(--color-text-tertiary)]">
          This view has no performance data wired up, so nothing is claimed about this
          file either way.
        </p>
      ) : items.length === 0 ? (
        <p className="text-xs text-[var(--color-text-tertiary)]">
          No open cause names this file as the place to intervene. The detectors are
          high precision and low recall, so that is not a measurement that it is fast.
        </p>
      ) : (
        <>
          <p className="text-xs text-[var(--color-text-secondary)]">
            <span className="tabular-nums">{formatNumber(page.total)}</span> open cause
            {page.total === 1 ? "" : "s"} name this file as the place to intervene
            {items.length < page.total ? (
              <>
                , <span className="tabular-nums">{items.length}</span> shown
              </>
            ) : null}
            . <span className="tabular-nums">{withPlan}</span> of those carry a stored plan.
          </p>
          <ul className="flex flex-col divide-y divide-[var(--color-border-default)]">
            {items.map((o) => (
              <CauseRow key={o.opportunity_id} opportunity={o} onSelect={onSelect} />
            ))}
          </ul>
        </>
      )}
    </section>
  );
}

/** Where a cause fires, from the evidence the queue already carries. */
function causeLocation(o: PerformanceOpportunity): string | null {
  if (o.terminal_sink) return o.terminal_sink;
  if (o.intervention_symbol) return o.intervention_symbol;
  const first = o.evidence[0];
  if (!first) return null;
  const name = first.function_name ?? null;
  if (name && first.line_start != null) return `${name}:${first.line_start}`;
  if (name) return name;
  return first.line_start != null ? `line ${first.line_start}` : null;
}

function CauseRow({
  opportunity: o,
  onSelect,
}: {
  opportunity: PerformanceOpportunity;
  onSelect?: ((opportunityId: string) => void) | undefined;
}) {
  const location = causeLocation(o);
  const body = (
    <>
      <span className="flex items-baseline gap-2">
        <span className="min-w-0 flex-1 text-xs font-medium text-[var(--color-text-primary)]">
          {biomarkerLabel(o.biomarker_type)}
        </span>
        <span className="shrink-0 font-mono text-[10px] uppercase tracking-wide text-[var(--color-text-tertiary)]">
          {ACTIONABILITY_LABEL[o.actionability_state]}
        </span>
      </span>
      {location ? (
        <span className="truncate font-mono text-[10px] text-[var(--color-text-secondary)]">
          {location}
        </span>
      ) : null}
      <span className="text-xs tabular-nums text-[var(--color-text-tertiary)]">
        {o.observations_total} observation{o.observations_total === 1 ? "" : "s"} ·{" "}
        {o.affected_files_total} file{o.affected_files_total === 1 ? "" : "s"} ·{" "}
        {o.plan_id ? "stored plan" : o.plan_reason}
      </span>
    </>
  );
  return (
    <li>
      {onSelect ? (
        <button
          type="button"
          onClick={() => onSelect(o.opportunity_id)}
          className="flex w-full flex-col gap-0.5 px-1 py-2 text-left transition-colors hover:bg-[var(--color-bg-selected)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-focus-ring)]"
        >
          {body}
        </button>
      ) : (
        <div className="flex flex-col gap-0.5 px-1 py-2">{body}</div>
      )}
    </li>
  );
}
