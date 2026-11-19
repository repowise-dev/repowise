import * as React from "react";

import { formatCost, formatTokens } from "../lib/format";
import type { SavingsCopy } from "./copy";
import { groupByJob, type SavingsJobId } from "./jobs";
import type { SavingsView } from "./types";

/**
 * Where the savings came from, as five plain jobs ranked by tokens. A job the
 * host says is not set up, and that recorded nothing, reads "Not set up" in
 * muted text with no button: the one nudge above the proof row is where an
 * action lives.
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
  const jobs = groupByJob(data.per_operation ?? []);
  const top = Math.max(...jobs.map((j) => j.savedInputTokens), 1);
  const priced = (data.priced_input_savings_usd ?? 0) > 0;

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
              ) : priced ? (
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
                  style={{ width: `${(100 * job.savedInputTokens) / top}%` }}
                />
              </div>
            )}
          </li>
        );
      })}
    </ul>
  );
}
