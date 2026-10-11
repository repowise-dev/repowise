"use client";

import { useState } from "react";
import { Check, ChevronDown, ChevronRight, Minus, Target, HelpCircle } from "lucide-react";
import type {
  DefectAccuracy,
  DefectAccuracyFile,
  DefectAccuracyPoint,
} from "@repowise-dev/types/health";
import { Card } from "../ui/card";

// Canonical definitions now live in @repowise-dev/types; re-exported here so
// existing `@repowise-dev/ui/health` consumers keep working unchanged.
export type { DefectAccuracy, DefectAccuracyFile, DefectAccuracyPoint };

/**
 * "Does the score actually find the buggy files?" card.
 *
 * Of the K lowest-health files, how many were touched by a bug-fix commit
 * in the recent window (`prior_defect` biomarker). We contrast that
 * precision against the repo-wide base rate (the lift) and let the user
 * expand a bulletproof breakdown: the per-K table, the concentration stat,
 * the exact flagged files, and an honest note on what the number is.
 *
 * The backend computes it from the same metrics + findings the page already
 * loads (`health/overview` -> `defect_accuracy`); it is null when the repo
 * lacks enough files or defect history to be honest, in which case the
 * caller should render nothing.
 */
export function DefectAccuracyCard({
  data,
  collapsible = false,
}: {
  data: DefectAccuracy;
  /** Start as a one-line "can you trust this score?" row that expands on click. */
  collapsible?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const [expanded, setExpanded] = useState(!collapsible);

  const months = Math.max(1, Math.round(data.window_days / 30));
  const windowLabel = months === 1 ? "month" : `${months} months`;
  const pct = Math.round(data.precision * 100);
  const basePct = Math.round(data.base_rate * 100);
  const concPct = Math.round(data.concentration_defect_share * 100);
  const concFilePct = Math.round(data.concentration_file_fraction * 100);

  if (!expanded) {
    return (
      <Card className="p-0">
        <button
          type="button"
          onClick={() => setExpanded(true)}
          aria-expanded={false}
          className="flex w-full flex-wrap items-center gap-2 px-4 py-3 text-left hover:bg-[var(--color-bg-elevated)] transition-colors rounded-lg"
        >
          <ChevronRight className="h-4 w-4 shrink-0 text-[var(--color-text-tertiary)]" aria-hidden />
          <Target className="h-4 w-4 shrink-0 text-[var(--color-accent-primary)]" aria-hidden />
          <span className="text-sm font-medium text-[var(--color-text-primary)]">
            Can you trust this score?
          </span>
          <span className="text-xs text-[var(--color-text-secondary)]">
            {data.hits}/{data.k} lowest-health files were bug-fixed in the last {windowLabel}
            {data.lift != null ? ` — ${data.lift}× the ${basePct}% baseline` : ""}
          </span>
        </button>
      </Card>
    );
  }

  return (
    <Card className="p-4">
      <div className="flex items-start justify-between gap-3">
        <div className="flex items-center gap-2">
          {collapsible ? (
            <button
              type="button"
              onClick={() => setExpanded(false)}
              aria-label="Collapse"
              aria-expanded
              className="text-[var(--color-text-tertiary)] hover:text-[var(--color-text-primary)]"
            >
              <ChevronDown className="h-4 w-4" aria-hidden />
            </button>
          ) : null}
          <Target className="h-4 w-4 text-[var(--color-accent-primary)]" aria-hidden />
          <h3 className="text-sm font-medium text-[var(--color-text-primary)]">
            {collapsible ? "Can you trust this score?" : "Does the health score find the bugs?"}
          </h3>
        </div>
        <button
          type="button"
          onClick={() => setOpen((v) => !v)}
          className="inline-flex items-center gap-1 text-[10px] uppercase tracking-wider text-[var(--color-text-tertiary)] hover:text-[var(--color-accent-primary)] transition shrink-0"
          aria-expanded={open}
          aria-controls="defect-accuracy-breakdown"
        >
          <HelpCircle className="h-3 w-3" aria-hidden />
          {open ? "Hide" : "How?"}
        </button>
      </div>

      {/* Headline */}
      <div className="mt-3 flex flex-wrap items-baseline gap-x-2 gap-y-1">
        <span className="text-2xl font-bold tabular-nums text-[var(--color-text-primary)]">
          {data.hits} / {data.k}
        </span>
        <span className="text-sm text-[var(--color-text-secondary)]">
          of the lowest-health files had a bug fix in the last {windowLabel}
        </span>
      </div>

      {data.lift != null && (
        <p className="mt-1.5 text-xs text-[var(--color-text-tertiary)]">
          That&apos;s{" "}
          <span className="font-semibold text-[var(--color-accent-primary)]">
            {data.lift}× the repo&apos;s {basePct}% baseline
          </span>{" "}
          — a random file has a {basePct}% chance; a file the score flags has {pct}%.
        </p>
      )}

      {open && (
        <div
          id="defect-accuracy-breakdown"
          className="mt-3 space-y-3 rounded-lg border border-[var(--color-border-default)] bg-[var(--color-bg-elevated)] p-3 text-xs"
        >
          <p className="leading-snug text-[var(--color-text-secondary)]">
            We rank every file by its health score, then check how many of the
            worst were touched by a bug-fix commit in the last {windowLabel}
            {" "}(from this repo&apos;s own git history).
          </p>

          {/* Per-K precision table */}
          <div className="space-y-1">
            {data.precision_table.map((row) => (
              <div key={row.k} className="flex items-center justify-between">
                <span className="text-[var(--color-text-tertiary)]">
                  Worst {row.k} files
                </span>
                <span className="tabular-nums text-[var(--color-text-primary)]">
                  {row.hits}/{row.k} were bug-fixed
                </span>
              </div>
            ))}
          </div>

          {/* Concentration */}
          <p className="rounded-md border border-[var(--color-border-default)] bg-[var(--color-bg-surface)] px-2 py-1.5 leading-snug text-[var(--color-text-secondary)]">
            The least-healthy <strong>{concFilePct}%</strong> of files contain{" "}
            <strong>{concPct}%</strong> of every recently bug-fixed file.
          </p>

          {/* Flagged files drill-down */}
          {data.flagged_files.length > 0 && (
            <div className="space-y-1">
              <p className="text-[10px] uppercase tracking-wider text-[var(--color-text-tertiary)]">
                The {data.k} flagged files
              </p>
              <ul className="max-h-48 space-y-0.5 overflow-y-auto pr-1">
                {data.flagged_files.map((f) => (
                  <li
                    key={f.file_path}
                    className="flex items-center justify-between gap-2"
                  >
                    <span className="flex min-w-0 items-center gap-1.5">
                      {f.recent_fixes > 0 ? (
                        <Check className="h-3 w-3 shrink-0 text-[var(--color-success)]" aria-label="bug-fixed" />
                      ) : (
                        <Minus className="h-3 w-3 shrink-0 text-[var(--color-text-tertiary)]" aria-label="no recent fix" />
                      )}
                      <span className="truncate font-mono text-xs text-[var(--color-text-secondary)]">
                        {f.file_path}
                      </span>
                    </span>
                    <span className="shrink-0 tabular-nums text-[10px] text-[var(--color-text-tertiary)]">
                      {f.score.toFixed(1)}
                    </span>
                  </li>
                ))}
              </ul>
            </div>
          )}

          <p className="text-[10px] leading-snug text-[var(--color-text-tertiary)]">
            Based on {data.defect_files} recently bug-fixed files out of{" "}
            {data.scored_files} scored. This is an association on the indexed
            history, not a forward prediction — recent bug-fix history is one of
            the score&apos;s many inputs.
          </p>
        </div>
      )}
    </Card>
  );
}
