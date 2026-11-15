import * as React from "react";
import type { OwnerFileEntry } from "@repowise-dev/types/owners";
import { EmptyState } from "../shared/empty-state";
import { PathText } from "../shared/path-text";
import { formatNumber } from "../lib/format";
import { RelativeTime } from "../shared/relative-time";
import { isSingleOwner } from "./knowledge-files";
import type { OwnershipLabels } from "./labels";

/**
 * The only marker on the file table.
 *
 * Sole ownership on its own marks 80% of this contributor's files, and a
 * hotspot flag 80% again, so either alone marks nothing. The conjunction — a
 * high-churn file, one author, and this person wrote at least 90% of its
 * commits — lands on a quarter of rows at most and describes one specific
 * situation worth acting on.
 */
function isSoleCarried(f: OwnerFileEntry): boolean {
  return f.is_hotspot && isSingleOwner(f.bus_factor) && (f.primary_owner_commit_pct ?? 0) >= 0.9;
}

export function soleCarriedCount(files: readonly OwnerFileEntry[]): number {
  return files.filter(isSoleCarried).length;
}

/**
 * The file table.
 *
 * Hand-rolled rather than `ResponsiveTable` for the same reason `OwnerTable`
 * is: that primitive is a client component, and nothing here needs one.
 *
 * Below `sm` it stops being a table. Five columns cannot share 390px without
 * starving the primary one, and the primary one is a file path that may not be
 * truncated — so the path takes the full width and the four figures collapse
 * into a single meta line beneath it.
 */
export function OwnerFileTable({
  files,
  hrefForFile,
  LinkComponent,
  labels,
}: {
  files: OwnerFileEntry[];
  hrefForFile?: ((filePath: string) => string) | undefined;
  LinkComponent?: React.ElementType | undefined;
  labels: OwnershipLabels;
}) {
  const A = LinkComponent ?? "a";

  if (files.length === 0) {
    return (
      <EmptyState
        className="p-6"
        title={labels.fileTableEmptyTitle}
        description={labels.fileTableEmptyDescription}
      />
    );
  }

  const headCls =
    "border-b border-[var(--color-border-default)] pb-2 font-mono text-[10px] font-normal uppercase tracking-[0.12em] text-[var(--color-text-tertiary)]";
  const numCls = "whitespace-nowrap py-2.5 pl-3 text-right align-top tabular-nums max-sm:hidden";

  return (
    <div className="-mx-[var(--page-pad)] overflow-x-auto px-[var(--page-pad)] sm:mx-0 sm:px-0">
      <table className="w-full border-collapse text-[13.5px]">
        <thead className="max-sm:hidden">
          <tr>
            <th scope="col" className={`${headCls} pr-3 text-left`}>
              {labels.colFile}
            </th>
            <th scope="col" className={`${headCls} whitespace-nowrap pl-3 text-right`}>
              {labels.colCommits90d}
            </th>
            <th
              scope="col"
              title={labels.colChurnHint}
              className={`${headCls} cursor-help whitespace-nowrap pl-3 text-right`}
            >
              {labels.colChurn}
            </th>
            <th
              scope="col"
              title={labels.colTheirCommitsHint}
              className={`${headCls} cursor-help whitespace-nowrap pl-3 text-right`}
            >
              {labels.colTheirCommits}
            </th>
            <th scope="col" className={`${headCls} whitespace-nowrap pl-3 text-right`}>
              {labels.colTouched}
            </th>
          </tr>
        </thead>
        <tbody>
          {files.map((f) => {
            const href = hrefForFile?.(f.file_path);
            const ownPct =
              f.primary_owner_commit_pct != null
                ? `${Math.round(f.primary_owner_commit_pct * 100)}%`
                : "—";
            return (
              <tr
                key={f.file_path}
                className="border-b border-[var(--color-border-default)] hover:bg-[var(--color-bg-elevated)] max-sm:block"
              >
                <td className="py-2.5 pr-3 align-top max-sm:block max-sm:pr-0">
                  {href ? (
                    <A href={href} className="group no-underline">
                      <PathText path={f.file_path} />
                    </A>
                  ) : (
                    <PathText path={f.file_path} />
                  )}
                  {isSoleCarried(f) && (
                    <span
                      title={labels.soleCarriedTitle}
                      className="ml-1.5 inline-block h-[5px] w-[5px] shrink-0 rounded-full bg-[var(--color-warning)] align-middle"
                    />
                  )}
                  {/* The four figures as one line, below `sm` only. Reads as a
                      sentence rather than as a table row that lost its header. */}
                  <span className="mt-1 hidden text-[11.5px] tabular-nums text-[var(--color-text-tertiary)] max-sm:block">
                    <RelativeTime
                      date={f.last_commit_at}
                      template={labels.fileMeta(f.commit_count_90d, Math.round(f.churn_percentile), ownPct, "{time}")}
                      fallback={labels.fileMeta(f.commit_count_90d, Math.round(f.churn_percentile), ownPct, "—")}
                    />
                  </span>
                </td>
                <td className={numCls}>{formatNumber(f.commit_count_90d)}</td>
                <td className={`${numCls} text-[var(--color-text-tertiary)]`}>
                  {Math.round(f.churn_percentile)}
                </td>
                <td className={`${numCls} text-[var(--color-text-tertiary)]`}>{ownPct}</td>
                <td className={`${numCls} text-[var(--color-text-tertiary)]`}>
                  <RelativeTime date={f.last_commit_at} fallback="—" />
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
