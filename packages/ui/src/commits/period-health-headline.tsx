import * as React from "react";
import type { PeriodHealthSummaryResponse } from "@repowise-dev/types/history";
import { PageLede } from "../shared/page-lede";
import { readPeriodHealth, type PeriodHealthRead } from "./period-health";
import { formatNumber } from "../lib/format";

/** Every sentence the headline can say. packages/ui has no i18n, so hosts
 *  pass their own; the defaults name commits in English. */
export interface PeriodHealthCopy {
  /** Quiet unit beside the figure: "commits made health better". */
  unit: (r: PeriodHealthRead) => string;
  /** Something improved and nothing got worse. */
  good: (r: PeriodHealthRead) => string;
  /** Both directions happened. The figure already names the improvements,
   *  so the sentence carries what got worse and what did nothing. */
  mixed: (r: PeriodHealthRead) => string;
  /** Analysed work that changed no finding. */
  quiet: (r: PeriodHealthRead) => string;
  /** Most of the window is not analysed yet: a calm progress line leads. */
  catchingUpUnit: (r: PeriodHealthRead) => string;
  catchingUp: (r: PeriodHealthRead) => string;
  /** The window holds no commits. */
  empty: (r: PeriodHealthRead) => string;
  /** How many items have no current analysis; shown only when some do not. */
  unscanned: (r: PeriodHealthRead) => string;
}

const s = (n: number) => (n === 1 ? "" : "s");

const DEFAULT_COPY: PeriodHealthCopy = {
  unit: (r) => `commit${s(r.better)} made health better`,
  good: (r) =>
    `Nothing made health worse. ${formatNumber(r.better)} commit${s(r.better)} made it better, resolving ${formatNumber(r.findingsBetter)} finding${s(r.findingsBetter)}.`,
  mixed: (r) =>
    `${formatNumber(r.worse)} made it worse, ${formatNumber(r.both)} of them while also making something better. ${formatNumber(r.neutral)} had no health impact.`,
  quiet: (r) =>
    `None of the ${formatNumber(r.analysed)} analysed commit${s(r.analysed)} changed a health finding.`,
  catchingUpUnit: (r) => `of ${formatNumber(r.total)} commit${s(r.total)} analysed so far`,
  catchingUp: () =>
    "Most commits in this window have not been analysed by the current analyzer yet. Recent commits come first, as updates run; the rows below show each older commit's direction from the earlier analysis.",
  empty: () => "No commits in this window.",
  unscanned: (r) => `${formatNumber(r.unscanned)} not yet analysed.`,
};

export interface PeriodHealthHeadlineProps {
  summary: PeriodHealthSummaryResponse;
  /** The window, as the micro-label names it: "Past month". */
  period: string;
  copy?: Partial<PeriodHealthCopy> | undefined;
  action?: React.ReactNode;
}

/**
 * The period headline: how many commits (or changes) made health better,
 * then how many made it worse. Improvements lead because they are the part a
 * team rarely sees; a window where nothing got worse says so in plain words.
 * Green only on a real improvement, never red: the regressions are a sentence,
 * and the list below is where they get worked.
 */
export function PeriodHealthHeadline({ summary, period, copy, action }: PeriodHealthHeadlineProps) {
  const c = { ...DEFAULT_COPY, ...copy };
  const r = readPeriodHealth(summary);
  if (r.tone === "catchingUp" || r.tone === "empty") {
    // No alarm and no green: progress, stated plainly.
    return (
      <PageLede label={period} value={formatNumber(r.analysed)} unit={c.catchingUpUnit(r)} action={action}>
        <p>{c[r.tone](r)}</p>
      </PageLede>
    );
  }
  return (
    <PageLede
      label={period}
      value={formatNumber(r.better)}
      valueColor={r.better > 0 ? "var(--color-success)" : undefined}
      unit={c.unit(r)}
      action={action}
    >
      <p>{c[r.tone](r)}</p>
      {r.unscanned > 0 && (
        <p className="text-[var(--color-text-tertiary)]">{c.unscanned(r)}</p>
      )}
    </PageLede>
  );
}
