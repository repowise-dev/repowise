import * as React from "react";

import { formatCost, formatTokens } from "../lib/format";
import type { SavingsCopy } from "./copy";
import { groupByJob, jobValue, type SavingsJobId } from "./jobs";
import type { SavingsView } from "./types";

/**
 * Where the savings came from, as five plain jobs ranked by what each row leads
 * with (dollars when the window has priced savings, else tokens). A job the
 * host says is not set up, and that recorded nothing, reads "Not set up" in
 * muted text with no button: the one nudge above the proof row is where an
 * action lives. A job with no priced event shows tokens, never $0.00.
 */
export function SavingsJobs({
  data,
  copy,
  notSetUp = [],
}: {
  data: SavingsView;
  copy: SavingsCopy;
  notSetUp?: readonly SavingsJobId[];
}) {
  // Ranked and sized by what each row leads with: dollars when anything in the
  // window was priced, tokens otherwise (subscription runs, or a host that
  // records no rates).
  const by = (data.priced_input_savings_usd ?? 0) > 0 ? "usd" : "tokens";
  const jobs = groupByJob(data.per_operation ?? [], by);
  const top = Math.max(...jobs.map((j) => jobValue(j, by)), Number.MIN_VALUE);

  return (
    <ul className="m-0 list-none p-0">
      {jobs.map((job) => {
        const empty = job.events === 0;
        const label = copy.jobs[job.id];
        return (
          <li
            key={job.id}
            className="grid grid-cols-[minmax(0,1fr)_auto] gap-x-4 gap-y-1.5 border-b border-[var(--color-border-subtle)] py-3.5"
          >
            <div className="min-w-0">
              <div
                className={`text-[15px] font-medium ${empty ? "text-[var(--color-text-tertiary)]" : "text-[var(--color-text-primary)]"}`}
              >
                {label.name}
              </div>
              <div className="text-xs text-[var(--color-text-tertiary)]">{label.detail}</div>
            </div>
            <div className="text-right tabular-nums">
              {empty ? (
                <span className="inline-flex items-center gap-1.5 font-mono text-[10px] font-medium uppercase tracking-[0.06em] text-[var(--color-text-tertiary)]">
                  <span aria-hidden className="h-1.5 w-1.5 rounded-full border border-[var(--color-text-tertiary)]" />
                  {notSetUp.includes(job.id) ? copy.jobNotSetUp : copy.jobNone}
                </span>
              ) : job.pricedInputUsd > 0 ? (
                <>
                  <div className="text-[15px] text-[var(--color-text-primary)]">{formatCost(job.pricedInputUsd)}</div>
                  <div className="font-mono text-xs text-[var(--color-text-tertiary)]">
                    {copy.jobTokens(formatTokens(job.savedInputTokens))}
                  </div>
                </>
              ) : (
                <div className="text-[15px] text-[var(--color-text-primary)]">
                  {copy.jobTokens(formatTokens(job.savedInputTokens))}
                </div>
              )}
            </div>
            {!empty && (
              <div className="col-span-2 h-[3px] overflow-hidden rounded-sm bg-[var(--color-border-subtle)]">
                <i
                  className="block h-full bg-[var(--color-text-tertiary)] opacity-60"
                  style={{ width: `${(100 * jobValue(job, by)) / top}%` }}
                />
              </div>
            )}
          </li>
        );
      })}
    </ul>
  );
}
