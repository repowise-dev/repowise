import * as React from "react";
import type { WinsResponse } from "@repowise-dev/types/history";
import { ReadsColumn, type ReadItem } from "../overview/reads-column";

/** Where each win links. The host owns routes. */
export interface RecentWinsRoutes {
  commit: (sha: string) => string;
  file: (path: string) => string;
  refactorings: string;
}

/** Every word the column says; English defaults, hosts pass translations. */
export interface RecentWinsCopy {
  title: string;
  findingsLabel: string;
  findingsUnit: (commits: number) => string;
  /** The commit that resolved most, as one line. */
  findingsWhy: (subject: string, resolved: number) => string;
  filesLabel: string;
  filesUnit: string;
  filesWhy: (path: string, before: number, after: number) => string;
  refactorsLabel: string;
  refactorsUnit: string;
  refactorsWhy: (symbol: string, before: number | null, after: number | null) => string;
  /** Said when the window holds no wins at all. */
  none: string;
}

const s = (n: number) => (n === 1 ? "" : "s");

const DEFAULT_COPY: RecentWinsCopy = {
  title: "Recent wins",
  findingsLabel: "Findings resolved",
  findingsUnit: (commits) => `across ${commits.toLocaleString()} commit${s(commits)}`,
  findingsWhy: (subject, resolved) => `Most in one commit: ${subject} (${resolved}).`,
  filesLabel: "Files got healthier",
  filesUnit: "score rose",
  filesWhy: (path, before, after) => `Biggest gain: ${path}, ${before.toFixed(1)} to ${after.toFixed(1)}.`,
  refactorsLabel: "Refactorings landed",
  refactorsUnit: "planned and done",
  refactorsWhy: (symbol, before, after) =>
    before != null && after != null
      ? `${symbol}: complexity ${before} to ${after}.`
      : `${symbol} was refactored as planned.`,
  none: "No wins recorded in this window yet. Resolved findings, healthier files and landed refactorings show up here as they happen.",
};

/**
 * The wins as rows of the shared reads column. Pure, so the Commits page and
 * Overview read one window the same way. Sections with nothing in them are
 * left out rather than shown as zero.
 */
export function winsReads(
  wins: WinsResponse,
  routes: RecentWinsRoutes,
  copy: RecentWinsCopy = DEFAULT_COPY,
): ReadItem[] {
  const commit = wins.findings_resolved.top_commits?.[0];
  const file = wins.files_improved.top?.[0];
  const refactor = wins.refactorings_landed.top?.[0];
  const sections: (Omit<ReadItem, "value"> & { count: number })[] = [
    {
      key: "findings",
      count: wins.findings_resolved.count,
      label: copy.findingsLabel,
      unit: copy.findingsUnit(wins.findings_resolved.commits),
      why: commit ? copy.findingsWhy(commit.subject, commit.resolved) : "",
      href: commit ? routes.commit(commit.sha) : routes.refactorings,
    },
    {
      key: "files",
      count: wins.files_improved.count,
      label: copy.filesLabel,
      unit: copy.filesUnit,
      why: file ? copy.filesWhy(file.path, file.before, file.after) : "",
      href: file ? routes.file(file.path) : routes.refactorings,
    },
    {
      key: "refactors",
      count: wins.refactorings_landed.count,
      label: copy.refactorsLabel,
      unit: copy.refactorsUnit,
      why: refactor
        ? copy.refactorsWhy(refactor.target_symbol, refactor.before_ccn ?? null, refactor.after_ccn ?? null)
        : "",
      href: routes.refactorings,
    },
  ];
  return sections
    .filter((s) => s.count > 0)
    .map(({ count, ...s }) => ({ ...s, value: count.toLocaleString(), good: true }));
}

export interface RecentWinsProps {
  wins: WinsResponse;
  routes: RecentWinsRoutes;
  /** The window and who, in words: "Past month, whole repository". */
  scope: string;
  copy?: Partial<RecentWinsCopy> | undefined;
  LinkComponent?: React.ElementType | undefined;
  className?: string | undefined;
}

/**
 * What got better in a window: findings resolved, files whose score rose and
 * refactorings that landed. Calm on purpose: a success dot and plain words,
 * no celebration. Reusable wherever a window of wins belongs (Commits,
 * Overview, a person page with `?author=`); the host fetches `/wins`.
 */
export function RecentWins({ wins, routes, scope, copy, LinkComponent, className }: RecentWinsProps) {
  const c = { ...DEFAULT_COPY, ...copy };
  const reads = winsReads(wins, routes, c);
  return (
    <section className={className} aria-label={c.title}>
      <div className="mb-3.5 flex items-baseline justify-between gap-3">
        <h2 className="shrink-0 text-[15px] font-semibold text-[var(--color-text-primary)]">{c.title}</h2>
        <span className="min-w-0 text-right text-[11px] text-[var(--color-text-tertiary)] [overflow-wrap:anywhere]">
          {scope}
        </span>
      </div>
      {reads.length > 0 ? (
        <ReadsColumn items={reads} LinkComponent={LinkComponent} />
      ) : (
        <p className="text-xs leading-relaxed text-[var(--color-text-tertiary)] [text-wrap:pretty]">
          {c.none}
        </p>
      )}
    </section>
  );
}
