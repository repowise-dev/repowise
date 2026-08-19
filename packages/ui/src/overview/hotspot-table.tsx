import * as React from "react";
import type { Hotspot } from "@repowise-dev/types/git";

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
 * The card this replaces rendered a churn bar per file that read "100%" for
 * every row, which is no information at all. The columns here are the ones
 * that actually rank a file: prior fixes and change frequency mined from full
 * git history, plus how many people maintain it.
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
  LinkComponent?: React.ElementType;
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
            <th scope="col" className="pb-2 pr-4 font-medium">Churn</th>
            <th scope="col" className="pb-2 pr-4 font-medium">Prior fixes</th>
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
                  {h.churn_percentile != null ? (
                    <>
                      {h.churn_percentile.toFixed(1)}
                      <span className="text-[var(--color-text-tertiary)]">th</span>
                    </>
                  ) : (
                    <span className="text-[var(--color-text-tertiary)]">—</span>
                  )}
                </td>
                <td className="py-2.5 pr-4 font-mono tabular-nums text-[var(--color-text-secondary)]">
                  {h.prior_defect_count ?? 0}
                  {h.bug_magnet && (
                    <span className="ml-1.5 whitespace-nowrap rounded-full border border-[color-mix(in_srgb,var(--color-error)_35%,transparent)] bg-[color-mix(in_srgb,var(--color-error)_8%,transparent)] px-1.5 py-0.5 font-sans text-[9.5px] font-medium text-[var(--color-error)]">
                      bug magnet
                    </span>
                  )}
                </td>
                <td className="py-2.5 pr-4 font-mono tabular-nums text-[var(--color-text-secondary)]">
                  {h.contributor_count ?? 0}
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
