"use client";

import * as React from "react";
import Link from "next/link";
import { Badge } from "../ui/badge";
import type {
  DecisionRecord,
  DecisionStatus,
  DecisionSource,
} from "@repowise-dev/types/decisions";

const STATUS_VARIANT: Record<string, "default" | "fresh" | "stale" | "outdated" | "outline" | "accent"> = {
  active: "fresh",
  proposed: "accent",
  deprecated: "outdated",
  superseded: "outline",
};

const SOURCE_LABEL: Record<string, string> = {
  inline_marker: "Inline",
  git_archaeology: "Git",
  readme_mining: "Docs",
  cli: "Manual",
};

export type DecisionStatusFilter = DecisionStatus | "all";
export type DecisionSourceFilter = DecisionSource | "all";

export interface DecisionsTableFilters {
  status: DecisionStatusFilter;
  source: DecisionSourceFilter;
}

export interface DecisionsTableProps {
  /** Resolved decision list. Caller fetches; the table renders. */
  decisions: DecisionRecord[] | undefined;
  /** Current filter values; the caller controls and reflects them in fetch keys. */
  filters: DecisionsTableFilters;
  /** Invoked when the user changes a filter. */
  onFiltersChange: (filters: DecisionsTableFilters) => void;
  /** Used to build the "View" link target for each row. */
  repoId: string;
  /** Truthy when the most recent fetch errored; an inline retry is rendered. */
  error?: unknown;
  /** Truthy while a fetch is in flight; suppresses the empty-state message. */
  isLoading?: boolean;
  /** Invoked when the user clicks "Retry" after an error. */
  onRetry?: () => void;
}

export function DecisionsTable({
  decisions,
  filters,
  onFiltersChange,
  repoId,
  error,
  isLoading,
  onRetry,
}: DecisionsTableProps) {
  return (
    <div className="space-y-4">
      {/* Filters */}
      <div className="flex flex-wrap gap-3">
        <select
          value={filters.status}
          onChange={(e) =>
            onFiltersChange({ ...filters, status: e.target.value as DecisionStatusFilter })
          }
          aria-label="Filter by status"
          className="w-full sm:w-auto rounded-md border border-[var(--color-border-default)] bg-[var(--color-bg-surface)] px-3 py-1.5 text-sm text-[var(--color-text-primary)]"
        >
          <option value="all">All statuses</option>
          <option value="active">Active</option>
          <option value="proposed">Proposed</option>
          <option value="deprecated">Deprecated</option>
          <option value="superseded">Superseded</option>
        </select>
        <select
          value={filters.source}
          onChange={(e) =>
            onFiltersChange({ ...filters, source: e.target.value as DecisionSourceFilter })
          }
          aria-label="Filter by source"
          className="w-full sm:w-auto rounded-md border border-[var(--color-border-default)] bg-[var(--color-bg-surface)] px-3 py-1.5 text-sm text-[var(--color-text-primary)]"
        >
          <option value="all">All sources</option>
          <option value="inline_marker">Inline markers</option>
          <option value="git_archaeology">Git archaeology</option>
          <option value="readme_mining">Docs mining</option>
          <option value="cli">Manual</option>
        </select>
      </div>

      {/* Table */}
      <div className="overflow-x-auto rounded-lg border border-[var(--color-border-default)]">
        <table className="w-full text-sm">
          <caption className="sr-only">Architectural decisions</caption>
          <thead className="sticky top-0 z-10 bg-[var(--color-bg-elevated)]">
            <tr className="border-b border-[var(--color-border-default)] bg-[var(--color-bg-elevated)]">
              <th scope="col" className="px-4 py-2.5 text-left font-medium text-[var(--color-text-secondary)]">Title</th>
              <th scope="col" className="px-4 py-2.5 text-left font-medium text-[var(--color-text-secondary)]">Status</th>
              <th scope="col" className="px-4 py-2.5 text-left font-medium text-[var(--color-text-secondary)]">Source</th>
              <th scope="col" className="px-4 py-2.5 text-right font-medium text-[var(--color-text-secondary)]">Confidence</th>
              <th scope="col" className="px-4 py-2.5 text-left font-medium text-[var(--color-text-secondary)]">Tags</th>
              <th scope="col" className="px-4 py-2.5 text-right font-medium text-[var(--color-text-secondary)]">Staleness</th>
            </tr>
          </thead>
          <tbody>
            {decisions?.map((d) => (
              <tr
                key={d.id}
                className={`border-b border-[var(--color-border-default)] transition-colors hover:bg-[var(--color-bg-elevated)] ${
                  d.status === "proposed" ? "border-l-2 border-l-amber-400" : ""
                }`}
              >
                <td className="px-4 py-2.5 min-w-[240px] max-w-[520px]">
                  <Link
                    href={`/repos/${repoId}/decisions/${d.id}`}
                    className="font-medium text-[var(--color-accent-primary)] hover:underline block truncate"
                    title={d.title}
                  >
                    {d.title}
                  </Link>
                </td>
                <td className="px-4 py-2.5">
                  <Badge variant={STATUS_VARIANT[d.status] ?? "outline"}>{d.status}</Badge>
                </td>
                <td className="px-4 py-2.5 text-[var(--color-text-secondary)]">
                  {SOURCE_LABEL[d.source] ?? d.source}
                </td>
                <td className="px-4 py-2.5 text-right tabular-nums text-[var(--color-text-secondary)]">
                  {Math.round(d.confidence * 100)}%
                </td>
                <td className="px-4 py-2.5">
                  <div className="flex flex-wrap gap-1">
                    {d.tags.slice(0, 3).map((tag) => (
                      <span
                        key={tag}
                        className="inline-block rounded bg-[var(--color-bg-elevated)] px-1.5 py-0.5 text-xs text-[var(--color-text-tertiary)]"
                      >
                        {tag}
                      </span>
                    ))}
                    {d.tags.length > 3 && (
                      <span
                        className="inline-block rounded bg-[var(--color-bg-elevated)] px-1.5 py-0.5 text-xs text-[var(--color-text-tertiary)]"
                        title={d.tags.slice(3).join(", ")}
                      >
                        +{d.tags.length - 3}
                      </span>
                    )}
                  </div>
                </td>
                <td className="px-4 py-2.5 text-right tabular-nums" title="0 = fresh, 1 = fully stale">
                  {d.staleness_score > 0.5 ? (
                    <span className="text-red-500">{Math.round(d.staleness_score * 100)}%</span>
                  ) : d.staleness_score > 0 ? (
                    <span className="text-[var(--color-text-tertiary)]">{Math.round(d.staleness_score * 100)}%</span>
                  ) : (
                    <span className="text-[var(--color-text-tertiary)]">â€”</span>
                  )}
                </td>
              </tr>
            ))}
            {!decisions?.length && Boolean(error) && !isLoading && (
              <tr>
                <td colSpan={6} className="px-4 py-8 text-center text-[var(--color-outdated)]">
                  Couldn&apos;t load decisions.{" "}
                  {onRetry && (
                    <button
                      onClick={onRetry}
                      className="underline text-[var(--color-accent-primary)] hover:text-[var(--color-text-primary)]"
                    >
                      Retry
                    </button>
                  )}
                </td>
              </tr>
            )}
            {!decisions?.length && !error && !isLoading && (
              <tr>
                <td colSpan={6} className="px-4 py-8 text-center text-[var(--color-text-tertiary)]">
                  No decisions found
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
