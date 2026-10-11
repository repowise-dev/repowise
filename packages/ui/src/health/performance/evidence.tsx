"use client";

import { useState } from "react";
import useSWR from "swr";
import type { HealthFinding, PerformanceOpportunityEvidence } from "@repowise-dev/types/health";

import { PaginationControls } from "../../shared/pagination-controls";
import { Skeleton, SkeletonRegion } from "../../ui/skeleton";
import type { PerformanceViewAdapter } from "./adapter";

/**
 * Raw observations behind one cause, fetched only when asked for.
 *
 * The rows carried on an opportunity are a bounded preview, so paging here
 * asks the server rather than growing the queue payload. A page is never
 * filtered on the client: it is a slice, and narrowing a slice would answer a
 * different question than the total printed beside it.
 */

const RAW_PAGE_SIZE = 50;

type EvidenceRow = HealthFinding | PerformanceOpportunityEvidence;

function rowKey(row: EvidenceRow, index: number): string {
  return ("id" in row ? row.id : row.finding_id) || String(index);
}

export function RawObservations({
  opportunityId,
  preview,
  previewTruncated,
  total,
  adapter,
}: {
  opportunityId: string;
  /** The bounded rows already on the opportunity. */
  preview: PerformanceOpportunityEvidence[];
  previewTruncated: boolean;
  total: number;
  adapter: PerformanceViewAdapter;
}) {
  const [open, setOpen] = useState(false);
  const [offset, setOffset] = useState(0);
  const load = adapter.getPerformanceOpportunityFindings;
  const { data, isLoading, error } = useSWR(
    open && load ? `performance-raw:${adapter.cacheKey}:${opportunityId}:${offset}` : null,
    () => load!(opportunityId, { offset, limit: RAW_PAGE_SIZE }),
    { revalidateOnFocus: false, keepPreviousData: true },
  );

  const rows: EvidenceRow[] = data?.items ?? preview;
  const reportedTotal = data?.total ?? total;

  return (
    <section>
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        aria-expanded={open}
        className="rounded text-sm font-medium text-[var(--color-text-secondary)] underline-offset-2 hover:text-[var(--color-text-primary)] hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent-primary)]"
      >
        {open ? "Hide" : "Read"} the raw observations
        <span className="tabular-nums"> ({reportedTotal.toLocaleString()})</span>
      </button>

      {open ? (
        <div className="mt-3">
          {error ? (
            <p className="text-sm text-[var(--color-text-tertiary)]">
              The observations could not be loaded. The preview below is what the queue already
              carried.
            </p>
          ) : null}
          {isLoading && !data ? (
            <SkeletonRegion label="Loading observations">
              <Skeleton className="h-24 rounded" />
            </SkeletonRegion>
          ) : (
            <ul className="divide-y divide-[var(--color-border-default)] border-y border-[var(--color-border-default)]">
              {rows.map((row, index) => (
                <li key={rowKey(row, index)} className="py-3">
                  <a
                    href={adapter.fileHref(row.file_path)}
                    className="break-all font-mono text-xs text-[var(--color-text-secondary)] underline-offset-2 hover:text-[var(--color-accent-primary)] hover:underline"
                  >
                    {row.file_path}
                    {row.line_start ? `:${row.line_start}` : ""}
                  </a>
                  <p className="mt-1 text-sm text-[var(--color-text-secondary)]">{row.reason}</p>
                </li>
              ))}
            </ul>
          )}

          {data ? (
            <PaginationControls
              offset={offset}
              shown={data.items.length}
              total={data.total}
              label="observations"
              onPrevious={
                offset > 0 ? () => setOffset(Math.max(0, offset - RAW_PAGE_SIZE)) : undefined
              }
              onNext={data.next_offset != null ? () => setOffset(data.next_offset!) : undefined}
            />
          ) : previewTruncated || preview.length < reportedTotal ? (
            <p className="mt-2 text-xs text-[var(--color-text-tertiary)]">
              Showing <span className="tabular-nums">{preview.length.toLocaleString()}</span> of{" "}
              <span className="tabular-nums">{reportedTotal.toLocaleString()}</span>. This host
              does not page the remaining observations.
            </p>
          ) : null}
        </div>
      ) : null}
    </section>
  );
}
