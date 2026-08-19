"use client";

import * as React from "react";
import type { StatsArrival } from "@repowise-dev/types/stats";
import { formatDate, formatNumber } from "../lib/format";

const INITIAL_SHOWN = 12;

/**
 * Contributors in the order they first showed up.
 *
 * The Contributors page shows one person's join date on their own profile, so
 * the sequence — who was here first, when the team grew — exists nowhere else.
 * Reads as a roll rather than a chart: the dates are the interesting part, and a
 * timeline axis would compress the early years into nothing on a repo whose
 * hiring accelerated.
 */
export function ArrivalsTimeline({ arrivals }: { arrivals: StatsArrival[] }) {
  const [expanded, setExpanded] = React.useState(false);
  const dated = (arrivals ?? []).filter((a) => a.first_commit_at);
  if (dated.length === 0) return null;

  const shown = expanded ? dated : dated.slice(0, INITIAL_SHOWN);
  const hidden = dated.length - shown.length;

  return (
    <section aria-label="Contributor arrivals" className="flex flex-col gap-4">
      <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
        <h3 className="text-base font-semibold text-[var(--color-text-primary)]">Arrivals</h3>
        <span className="font-mono text-[11px] tabular-nums text-[var(--color-text-tertiary)]">
          {formatNumber(dated.length)} contributors
        </span>
      </div>

      <ol className="flex flex-col divide-y divide-[var(--color-border-default)] border-y border-[var(--color-border-default)]">
        {shown.map((a, i) => (
          <li key={`${a.name}-${a.first_commit_at}`} className="flex items-baseline gap-3 py-2.5">
            <span className="w-6 shrink-0 font-mono text-[11px] tabular-nums text-[var(--color-text-tertiary)]">
              {i + 1}
            </span>
            <span className="min-w-0 flex-1 truncate text-sm text-[var(--color-text-primary)]">
              {a.name}
            </span>
            <span className="shrink-0 font-mono text-[11px] tabular-nums text-[var(--color-text-secondary)]">
              {formatDate(a.first_commit_at as string)}
            </span>
          </li>
        ))}
      </ol>

      {hidden > 0 && (
        <button
          type="button"
          onClick={() => setExpanded(true)}
          className="self-start rounded-md px-1 text-xs font-medium text-[var(--color-accent-primary)] underline-offset-4 hover:underline focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--color-accent-primary)]"
        >
          Show {formatNumber(hidden)} more
        </button>
      )}
    </section>
  );
}
