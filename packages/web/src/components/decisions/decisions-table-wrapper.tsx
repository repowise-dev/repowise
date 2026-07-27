"use client";

import * as React from "react";
import useSWR from "swr";
import Link from "next/link";
import {
  DecisionsTable,
  type DecisionsTableFilters,
} from "@repowise-dev/ui/decisions/decisions-table";
import { getDecisionCounts, listDecisions } from "@/lib/api/decisions";
import type { DecisionRecord } from "@repowise-dev/types/decisions";

interface DecisionsTableWrapperProps {
  repoId: string;
  initialData?: DecisionRecord[];
  /** Measured total for the unfiltered set, from the server's COUNT. */
  initialTotal?: number;
  pageSize?: number;
}

/**
 * Data wrapper for the decisions table.
 *
 * Pages on the server. It used to pull `limit: 100` in one shot and render
 * every row: on a repository with several hundred records that is ~920KB of
 * JSON for a table nobody scrolls past the top of, and it still could not show
 * the rest. The window is now `pageSize` rows and the count beside it comes
 * from the aggregate endpoint, so "51–100 of 500" is measured on both sides.
 */
export function DecisionsTableWrapper({
  repoId,
  initialData,
  initialTotal,
  pageSize = 50,
}: DecisionsTableWrapperProps) {
  const [filters, setFilters] = React.useState<DecisionsTableFilters>({
    status: "all",
    source: "all",
  });
  const [page, setPage] = React.useState(0);

  const status = filters.status !== "all" ? filters.status : undefined;
  const source = filters.source !== "all" ? filters.source : undefined;

  // A filter change invalidates the offset — page 3 of "all" is not page 3 of
  // "active", and leaving it put lands the reader on an empty table.
  const handleFiltersChange = React.useCallback((next: DecisionsTableFilters) => {
    setFilters(next);
    setPage(0);
  }, []);

  const { data, error, mutate, isLoading } = useSWR(
    [`/api/repos/${repoId}/decisions`, status, source, page, pageSize],
    () =>
      listDecisions(repoId, {
        ...(status ? { status } : {}),
        ...(source ? { source } : {}),
        include_proposed: true,
        limit: pageSize,
        offset: page * pageSize,
      }),
    { fallbackData: page === 0 && !status && !source ? initialData : undefined },
  );

  // Counts follow the filters, because "of 500" beside a filtered table is the
  // wrong denominator. Source is a server filter so it goes in the key; status
  // is read off the returned per-status counts.
  const { data: counts } = useSWR(
    [`/api/repos/${repoId}/decisions/counts`, source],
    () =>
      getDecisionCounts(repoId, {
        ...(source ? { source } : {}),
        include_proposed: true,
      }),
  );

  const total = counts
    ? filters.status === "all"
      ? counts.total
      : (counts[filters.status] ?? 0)
    : !source && filters.status === "all"
      ? initialTotal
      : undefined;

  const rows = data ?? [];
  const first = page * pageSize + 1;
  const last = page * pageSize + rows.length;
  const hasNext =
    total !== undefined ? last < total : rows.length === pageSize;

  return (
    <div className="space-y-4">
      <DecisionsTable
        decisions={data}
        filters={filters}
        onFiltersChange={handleFiltersChange}
        repoId={repoId}
        LinkComponent={Link}
        error={error}
        isLoading={isLoading}
        onRetry={() => mutate()}
      />

      {(page > 0 || hasNext) && (
        <div className="flex items-center justify-between gap-4 border-t border-[var(--color-border-default)] pt-3">
          <p className="font-mono text-[11px] tabular-nums text-[var(--color-text-tertiary)]">
            {rows.length > 0 ? `${first}–${last}` : "0"}
            {total !== undefined ? ` of ${total.toLocaleString()}` : ""}
          </p>
          <div className="flex items-center gap-2">
            <PageButton
              onClick={() => setPage((p) => Math.max(0, p - 1))}
              disabled={page === 0 || isLoading}
            >
              Previous
            </PageButton>
            <PageButton
              onClick={() => setPage((p) => p + 1)}
              disabled={!hasNext || isLoading}
            >
              Next
            </PageButton>
          </div>
        </div>
      )}
    </div>
  );
}

function PageButton({
  onClick,
  disabled,
  children,
}: {
  onClick: () => void;
  disabled: boolean;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      className="rounded-md border border-[var(--color-border-default)] px-2.5 py-1 text-xs text-[var(--color-text-secondary)] transition-colors hover:border-[var(--color-border-hover)] hover:text-[var(--color-text-primary)] disabled:opacity-40 disabled:hover:border-[var(--color-border-default)] disabled:hover:text-[var(--color-text-secondary)]"
    >
      {children}
    </button>
  );
}
