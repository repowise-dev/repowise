"use client";

import * as React from "react";
import { Badge } from "../ui/badge";
import { ApiError } from "../shared/api-error";
import { EmptyState } from "../shared/empty-state";
import {
  ResponsiveTable,
  type ResponsiveColumn,
} from "../shared/responsive-table";
import { VerificationBadge } from "./verification-badge";
import { stripMarkdown } from "../lib/format";
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
  linkPrefix?: string;
  LinkComponent?: React.ElementType<{ href: string; className?: string; children: React.ReactNode }>;
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
  linkPrefix,
  LinkComponent = "a",
  error,
  isLoading,
  onRetry,
}: DecisionsTableProps) {
  const prefix = linkPrefix ?? `/repos/${repoId}`;
  const Link = LinkComponent;

  const columns: ResponsiveColumn<DecisionRecord>[] = [
    {
      key: "title",
      header: "Title",
      priority: 1,
      cellClassName: "min-w-[200px] max-w-[520px]",
      render: (d) => (
        <div className="min-w-0">
          <Link
            href={`${prefix}/decisions/${d.id}`}
            className="font-medium text-[var(--color-text-primary)] hover:text-[var(--color-accent-primary)] hover:underline block truncate"
            title={stripMarkdown(d.title)}
          >
            {stripMarkdown(d.title)}
          </Link>
          {d.evidence_preview?.source_quote && (
            <p
              className="mt-0.5 truncate text-xs italic text-[var(--color-text-tertiary)]"
              title={`${d.evidence_preview.source_quote}${
                d.evidence_preview.evidence_file
                  ? ` — ${d.evidence_preview.evidence_file}${
                      d.evidence_preview.evidence_line != null
                        ? `:${d.evidence_preview.evidence_line}`
                        : ""
                    }`
                  : ""
              }`}
            >
              “{d.evidence_preview.source_quote}”
              {(d.evidence_count ?? 0) > 1 && (
                <span className="ml-1 not-italic">
                  +{(d.evidence_count ?? 0) - 1} more
                </span>
              )}
            </p>
          )}
        </div>
      ),
    },
    {
      key: "status",
      header: "Status",
      priority: 1,
      render: (d) => <Badge variant={STATUS_VARIANT[d.status] ?? "outline"}>{d.status}</Badge>,
    },
    {
      key: "source",
      header: "Source",
      priority: 3,
      cellClassName: "text-[var(--color-text-secondary)]",
      render: (d) => SOURCE_LABEL[d.source] ?? d.source,
    },
    {
      key: "trust",
      header: "Trust",
      priority: 3,
      render: (d) =>
        d.verification ? (
          <VerificationBadge verification={d.verification} iconOnly />
        ) : (
          <span className="text-[var(--color-text-tertiary)]">—</span>
        ),
    },
    {
      key: "confidence",
      header: "Confidence",
      mobileLabel: "Conf",
      align: "right",
      priority: 2,
      cellClassName: "tabular-nums text-[var(--color-text-secondary)]",
      render: (d) => `${Math.round(d.confidence * 100)}%`,
    },
    {
      key: "tags",
      header: "Tags",
      priority: 3,
      render: (d) => (
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
      ),
    },
    {
      key: "staleness",
      header: "Staleness",
      mobileLabel: "Stale",
      align: "right",
      priority: 2,
      render: (d) => (
        <span className="tabular-nums" title="0 = fresh, 1 = fully stale">
          {d.staleness_score > 0.5 ? (
            <span className="text-[var(--color-error)]">{Math.round(d.staleness_score * 100)}%</span>
          ) : d.staleness_score > 0 ? (
            <span className="text-[var(--color-text-tertiary)]">{Math.round(d.staleness_score * 100)}%</span>
          ) : (
            <span className="text-[var(--color-text-tertiary)]">—</span>
          )}
        </span>
      ),
    },
  ];

  const empty =
    isLoading ? undefined : error ? (
      <ApiError
        title="Couldn't load decisions"
        message="An error occurred while fetching decisions."
        {...(onRetry ? { onRetry } : {})}
      />
    ) : (
      <EmptyState
        title="No decisions found"
        description="No architectural decisions match the current filters."
      />
    );

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

      <ResponsiveTable
        columns={columns}
        rows={decisions ?? []}
        rowKey={(d) => d.id}
        empty={empty}
      />
    </div>
  );
}
