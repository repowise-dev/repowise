import * as React from "react";
import type { Hotspot } from "@repowise-dev/types/git";
import { formatRelativeTime, formatDateTime } from "../lib/format";

function fileName(path: string): string {
  const i = path.lastIndexOf("/");
  return i === -1 ? path : path.slice(i + 1);
}

function fileDir(path: string): string {
  const i = path.lastIndexOf("/");
  return i === -1 ? "" : path.slice(0, i);
}

/**
 * Where the risk concentrates, as a table rather than a mini card.
 *
 * Every column has to vary across the rows, or it is decoration. The card
 * this replaced rendered a churn bar that read "100%" on every row; the churn
 * *percentile* column that succeeded it had the same defect for the same
 * reason — this is the top of a ranking whose primary key is churn, so the
 * five rows read 100.0th, 100.0th, 99.9th, 99.9th, 99.9th and rank nothing.
 * It is replaced by the date of the last fix, which varies, and which the
 * `bug_magnet` field is contractually required to appear beside: that flag is
 * a decayed, recency-weighted claim, so a copy showing it without saying when
 * is asserting something it has not shown.
 *
 * Placed mid-page, not at the top. On the public landing page this table is
 * the proof that the product does something git-history-shaped, and a stranger
 * needs that proof up front. On your own repo it barely moves week to week, so
 * it is reference material — but it stays, because OSS users have no other
 * surface where this is ever shown.
 */
export function HotspotTable({
  hotspots,
  hrefFor,
  LinkComponent,
}: {
  hotspots: Hotspot[];
  /** Builds the per-file link. Callers differ on route shape, so it is a fn. */
  hrefFor: (path: string) => string;
  LinkComponent?: React.ElementType | undefined;
}) {
  const A = LinkComponent ?? "a";
  if (hotspots.length === 0) return null;

  return (
    // Horizontal scroll is scoped to the table so the page body never scrolls
    // sideways on a phone; the negative margin lets it bleed to the edge there.
    <div className="-mx-[var(--page-pad)] overflow-x-auto px-[var(--page-pad)] sm:mx-0 sm:px-0">
      <table className="w-full min-w-[560px] border-collapse text-xs">
        <thead>
          <tr className="border-b border-[var(--color-border-default)] text-left font-mono text-[9.5px] uppercase tracking-[0.12em] text-[var(--color-text-tertiary)]">
            <th scope="col" className="pb-2 pr-4 font-medium">File</th>
            <th scope="col" className="pb-2 pr-4 font-medium">Prior fixes</th>
            <th scope="col" className="pb-2 pr-4 font-medium">Last fix</th>
            <th scope="col" className="pb-2 pr-4 font-medium">Maintainers</th>
            <th scope="col" className="pb-2 text-right font-medium">Commits 90d</th>
          </tr>
        </thead>
        <tbody>
          {hotspots.map((h) => {
            const dir = fileDir(h.file_path);
            return (
              <tr
                key={h.file_path}
                className="border-b border-[var(--color-border-default)] align-top last:border-b-0"
              >
                <td className="py-2.5 pr-4">
                  <A
                    href={hrefFor(h.file_path)}
                    className="group block min-w-0 no-underline"
                  >
                    <span className="block font-medium text-[var(--color-text-primary)] group-hover:text-[var(--color-accent-primary)]">
                      {fileName(h.file_path)}
                    </span>
                    {dir && (
                      <span className="mt-0.5 block break-all font-mono text-[10px] text-[var(--color-text-tertiary)]">
                        {dir}
                      </span>
                    )}
                  </A>
                </td>
                <td className="py-2.5 pr-4 font-mono tabular-nums text-[var(--color-text-secondary)]">
                  {h.prior_defect_count ?? 0}
                  {/* Quiet, and no longer a red pill. Rule 7 is "mark
                      exceptions, not the default": this is a table OF bug
                      magnets, so on most rows the flag is the reason the row
                      is here rather than news about it, and an alarm colour on
                      four rows of five is the loudest thing on the page saying
                      the least. Red is also spoken for — it carries a health
                      band everywhere else, and a reader arriving from Code
                      Health reads it as a score. */}
                  {h.bug_magnet && (
                    <span className="ml-1.5 whitespace-nowrap font-sans text-[10px] text-[var(--color-text-tertiary)]">
                      bug magnet
                    </span>
                  )}
                </td>
                <td className="py-2.5 pr-4 font-mono tabular-nums text-[var(--color-text-secondary)]">
                  {h.last_fix_at ? (
                    <span title={formatDateTime(h.last_fix_at)}>
                      {formatRelativeTime(h.last_fix_at)}
                    </span>
                  ) : (
                    <span className="text-[var(--color-text-tertiary)]">—</span>
                  )}
                </td>
                <td className="py-2.5 pr-4 font-mono tabular-nums text-[var(--color-text-secondary)]">
                  {h.contributor_count ?? 0}
                  {/* This one keeps its colour: a single maintainer is a
                      genuine exception among these rows, not the reason they
                      were selected. */}
                  {h.bus_factor === 1 && (
                    <span className="mt-0.5 block whitespace-nowrap font-sans text-[10px] text-[var(--color-warning)]">
                      bus factor 1
                    </span>
                  )}
                </td>
                <td className="py-2.5 text-right font-mono tabular-nums text-[var(--color-text-secondary)]">
                  {h.commit_count_90d ?? 0}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
