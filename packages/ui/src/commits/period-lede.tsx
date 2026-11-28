import * as React from "react";
import type { PeriodHealthSummaryResponse, WinsResponse } from "@repowise-dev/types/history";
import { PageLede } from "../shared/page-lede";
import { readPeriodHealth, type PeriodHealthRead } from "./period-health";
import { formatNumber } from "../lib/format";

/** The window's work in numbers, read from the commit health summary. */
export interface PeriodWorkRead {
  total: number;
  /** The largest category and its share of the window, 0-100; null when the server sent none. */
  top: { category: string; count: number; share: number } | null;
  /** The runner-up, when it holds a fifth of the window or more. */
  second: { category: string; share: number } | null;
  health: PeriodHealthRead;
  /** Which way the findings moved across the analysed commits. */
  direction: "better" | "worse" | "flat" | "unknown";
}

type SummaryWithCategories = PeriodHealthSummaryResponse & { categories?: Record<string, number> | null };

export function readPeriodWork(summary: SummaryWithCategories): PeriodWorkRead {
  const health = readPeriodHealth(summary);
  const ranked = Object.entries(summary.categories ?? {}).sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]));
  const sum = ranked.reduce((n, [, c]) => n + c, 0);
  const share = (c: number) => (sum > 0 ? Math.round((c / sum) * 100) : 0);
  const [first, next] = ranked;
  let direction: PeriodWorkRead["direction"] = "unknown";
  if (health.analysed > 0) {
    if (health.findingsBetter > health.findingsWorse) direction = "better";
    else if (health.findingsWorse > health.findingsBetter) direction = "worse";
    else direction = "flat";
  }
  return {
    total: summary.total,
    top: first ? { category: first[0], count: first[1], share: share(first[1]) } : null,
    second: next && share(next[1]) >= 20 ? { category: next[0], share: share(next[1]) } : null,
    health,
    direction,
  };
}

/** Every sentence the lede can say; English defaults, hosts pass translations. */
export interface PeriodLedeCopy {
  unit: (r: PeriodWorkRead) => string;
  /** "57% were fixes, then features at 22%." */
  mix: (r: PeriodWorkRead, top: string, second: string | null) => string;
  better: (r: PeriodWorkRead) => string;
  worse: (r: PeriodWorkRead) => string;
  flat: (r: PeriodWorkRead) => string;
  /** The single commit that resolved the most. */
  notable: (subject: string, resolved: number) => string;
  /** Quiet coverage line, only when some commits are not analysed yet. */
  coverage: (r: PeriodWorkRead) => string;
  empty: () => string;
  /** A category id in words, plural: "fixes". */
  category: (id: string) => string;
}

const CATEGORY_WORDS: Record<string, string> = {
  feature: "features",
  fix: "fixes",
  refactor: "refactors",
  docs: "docs changes",
  test: "test changes",
  deps: "dependency updates",
  chore: "chores",
  other: "other work",
};

const plural = (n: number, one: string, many = `${one}s`) => `${formatNumber(n)} ${n === 1 ? one : many}`;

const DEFAULT_COPY: PeriodLedeCopy = {
  unit: (r) => (r.total === 1 ? "commit" : "commits"),
  mix: (r, top, second) =>
    `${r.top?.share ?? 0}% were ${top}${second && r.second ? `, then ${second} at ${r.second.share}%` : ""}.`,
  better: (r) =>
    `Health moved the right way: ${plural(r.health.findingsBetter, "finding")} resolved or improved, ${formatNumber(r.health.findingsWorse)} added or worsened.`,
  worse: (r) =>
    `Health slipped: ${plural(r.health.findingsWorse, "finding")} added or worsened, ${formatNumber(r.health.findingsBetter)} resolved or improved.`,
  flat: (r) =>
    r.health.findingsBetter === 0
      ? "No analysed commit changed a health finding."
      : `Health held even: ${plural(r.health.findingsBetter, "finding")} resolved or improved and as many added or worsened.`,
  notable: (subject, resolved) => `Biggest single cleanup: ${subject}, ${plural(resolved, "finding")} resolved.`,
  coverage: (r) =>
    `Health counts cover the ${formatNumber(r.health.analysed)} of ${formatNumber(r.total)} commits the current analyzer has read; the rest fill in as updates run.`,
  empty: () => "No commits in this window.",
  category: (id) => CATEGORY_WORDS[id] ?? id,
};

export interface PeriodLedeProps {
  summary: SummaryWithCategories;
  /** The window's wins, for the notable change; omit when the lede is narrowed past what the wins follow. */
  wins?: WinsResponse | null | undefined;
  /** The window, as the micro-label names it: "Past month". */
  period: string;
  /** Where the notable commit links. */
  commitHref?: ((sha: string) => string) | undefined;
  LinkComponent?: React.ElementType | undefined;
  copy?: Partial<PeriodLedeCopy> | undefined;
}

function directionSentence(c: PeriodLedeCopy, r: PeriodWorkRead): string {
  if (r.direction === "better") return c.better(r);
  if (r.direction === "worse") return c.worse(r);
  if (r.direction === "flat") return c.flat(r);
  return "";
}

/**
 * What happened in the window: how many commits, what kind of work dominated,
 * which way health moved, and the change worth knowing about. Health counts
 * read only analysed commits, and when some are not analysed yet a quiet line
 * says how many are covered: the lede never implies full coverage.
 */
export function PeriodLede({ summary, wins, period, commitHref, LinkComponent = "a", copy }: PeriodLedeProps) {
  const c = { ...DEFAULT_COPY, ...copy };
  const r = readPeriodWork(summary);
  if (r.total === 0) {
    return (
      <PageLede label={period} value="0" unit={c.unit(r)}>
        <p>{c.empty()}</p>
      </PageLede>
    );
  }
  const notable = wins?.findings_resolved.top_commits?.[0];
  const notableText = notable ? c.notable(notable.subject, notable.resolved) : null;
  const mix = r.top ? `${c.mix(r, c.category(r.top.category), r.second ? c.category(r.second.category) : null)} ` : "";
  const Link = LinkComponent;
  return (
    <PageLede label={period} value={formatNumber(r.total)} unit={c.unit(r)}>
      <p>
        {mix}
        {directionSentence(c, r)}
      </p>
      {notable && (
        <p>
          {commitHref ? (
            <Link href={commitHref(notable.sha)} className="hover:underline">
              {notableText}
            </Link>
          ) : (
            notableText
          )}
        </p>
      )}
      {r.health.unscanned > 0 && (
        // When most commits are not analysed yet the direction rests on a
        // few of them, so the coverage line reads at full weight.
        <p className={r.health.tone === "catchingUp" ? undefined : "text-xs text-[var(--color-text-tertiary)]"}>
          {c.coverage(r)}
        </p>
      )}
    </PageLede>
  );
}
