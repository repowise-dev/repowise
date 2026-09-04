"use client";

import * as React from "react";
import { ApiError } from "../shared/api-error";
import { EmptyState } from "../shared/empty-state";
import {
  ResponsiveTable,
  type ResponsiveColumn,
} from "../shared/responsive-table";
import { VerificationBadge } from "./verification-badge";
import { DecisionStatusMark } from "./decision-status-mark";
import { describeRecordStaleness } from "./decision-staleness";
import { stripMarkdown } from "../lib/format";
import {
  DECISION_SOURCES,
  DECISION_STATUSES,
  DECISION_STATUS_LABELS,
  decisionSourceLabel,
  isRetiredDecisionSource,
} from "@repowise-dev/types/decisions";
import type {
  DecisionRecord,
  DecisionStatus,
  DecisionSource,
  DecisionScope,
} from "@repowise-dev/types/decisions";

export type DecisionStatusFilter = DecisionStatus | "all";
export type DecisionSourceFilter = DecisionSource | "all";
export type DecisionScopeFilter = DecisionScope | "all";

export interface DecisionsTableFilters {
  status: DecisionStatusFilter;
  source: DecisionSourceFilter;
  /**
   * Optional for back-compat with callers that predate scope. Unlike
   * status/source (server query params), scope is derived at serialization
   * time, so the table filters rows client-side.
   */
  scope?: DecisionScopeFilter;
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

  // Scope is derived at serialization time (no server-side query param), so
  // this filter applies client-side to the fetched rows. Backends that don't
  // serve scope yet leave every row null: hide the filter (it could only
  // empty the table) and ignore any lingering scope value.
  const scopeFilter = filters.scope ?? "all";
  const hasScope = (decisions ?? []).some((d) => d.scope != null);
  const visibleDecisions = (decisions ?? []).filter(
    (d) => !hasScope || scopeFilter === "all" || d.scope === scopeFilter,
  );

  // The hardcoded list offered `readme_mining` and `cli` while omitting `pr`
  // and `session`, which between them are 86% of a live index. Filtering is a
  // server round trip on the whole store, so the two live sources could not
  // be reached at all and the retired one could only empty the table.
  //
  // Built from the shared source list rather than from the loaded rows on
  // purpose: the rows are one page of fifty, so a source that happens not to
  // appear on page one would become unselectable, which is the same defect
  // wearing a dynamic coat. Anything present but unlisted is unioned in so a
  // source the engine adds is reachable before this build learns its name.
  const sourceOptions = [
    ...new Set([
      ...DECISION_SOURCES,
      ...(decisions ?? []).map((d) => d.source).filter(Boolean),
    ]),
  ]
    .filter((s) => !isRetiredDecisionSource(s))
    .sort((a, b) =>
      decisionSourceLabel(a).localeCompare(decisionSourceLabel(b)),
    );

  const columns: ResponsiveColumn<DecisionRecord>[] = [
    {
      key: "title",
      header: "Title",
      priority: 1,
      cellClassName: "min-w-[200px] max-w-[520px]",
      render: (d) => (
        <div className="min-w-0">
          <span className="flex min-w-0 items-center gap-1.5">
            <Link
              href={`${prefix}/decisions/${d.id}`}
              className="font-medium text-[var(--color-text-primary)] hover:text-[var(--color-accent-primary)] hover:underline truncate"
              title={stripMarkdown(d.title)}
            >
              {stripMarkdown(d.title)}
            </Link>
            {d.verification && d.verification !== "exact" && (
              <VerificationBadge verification={d.verification} iconOnly />
            )}
          </span>
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
      render: (d) => <DecisionStatusMark status={d.status} />,
    },
    {
      key: "source",
      header: "Source",
      priority: 3,
      cellClassName: "text-[var(--color-text-secondary)]",
      render: (d) => decisionSourceLabel(d.source),
    },
    // Scope, Confidence and Trust all left this row. Scope is `cross-module`
    // on three quarters of a live index and confidence is source rank times
    // verification, so every record from one source carries one number: all
    // twelve session records read 84%. A column whose value you can predict
    // from the column beside it spends width repeating itself.
    //
    // Trust went for a different reason. 91% of a live index verifies
    // `exact`, so the mark belongs on the exception rather than on every row
    // (it is now inline beside the title above). Making the *column* appear
    // only when a loaded row needs it was the first attempt and was worse:
    // `visibleDecisions` is one page of fifty, so the table grew and lost a
    // column between clicks of Next, and a reader who saw a mark on page one
    // had no way to tell page two had even been asked. That is the same
    // defect the source filter avoids ten lines above.
    {
      key: "tags",
      header: "Tags",
      priority: 3,
      render: (d) => (
        <div className="flex flex-wrap gap-x-2 gap-y-0.5">
          {d.tags.slice(0, 3).map((tag) => (
            <span
              key={tag}
              className="font-mono text-[11px] text-[var(--color-text-tertiary)]"
            >
              {tag}
            </span>
          ))}
          {d.tags.length > 3 && (
            <span
              className="font-mono text-[11px] tabular-nums text-[var(--color-text-tertiary)]"
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
      // Not "Files changed since". The score's numerator also counts a file
      // the repository no longer tracks, so a record naming three deleted
      // paths would read as three files that changed. "Scope changed" is the
      // claim the number supports; the tooltip carries the whole sentence.
      header: "Scope changed",
      mobileLabel: "Changed",
      align: "right",
      priority: 2,
      // Was a percentage with a "0 = fresh, 1 = fully stale" tooltip, and the
      // percentage was wrong twice over. Its zero was one dash shared by a
      // record whose files had not moved and a record naming no files at all,
      // and its red above 0.5 painted three confirmed working rules as
      // expired because the files they cite happened to change. Red is
      // reserved for health bands; this is a count, so it reads as one, in
      // mono like every other machine-produced figure.
      render: (d) => {
        const s = describeRecordStaleness(d);
        return (
          <span
            className={
              s.kind === "moved"
                ? "font-mono text-xs tabular-nums text-[var(--color-text-secondary)]"
                : "font-mono text-xs tabular-nums text-[var(--color-text-tertiary)]"
            }
            title={s.sentence}
          >
            {s.short}
          </span>
        );
      },
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
          {/* From the shared ladder, so a status the engine adds is reachable
              without editing this list. Dismissed is in it: dismissed records
              are excluded from every default listing, so without the option a
              dismissal would be a one-way door with nothing in the UI able to
              show what had been dismissed, or undo one. */}
          {DECISION_STATUSES.map((s) => (
            <option key={s} value={s}>
              {DECISION_STATUS_LABELS[s]}
            </option>
          ))}
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
          {sourceOptions.map((s) => (
            <option key={s} value={s}>
              {decisionSourceLabel(s)}
            </option>
          ))}
        </select>
        {/* The scope control is gone with the scope column. It steered an axis
            that is `cross-module` on three quarters of a live index, and with
            the column no longer drawn a reader who used it would watch rows
            disappear for a reason nothing on screen explains. The prop and the
            filtering below it are kept, so a host still passing `scope` gets
            the same rows it did before. */}
      </div>

      <ResponsiveTable
        columns={columns}
        rows={visibleDecisions}
        rowKey={(d) => d.id}
        empty={empty}
      />
    </div>
  );
}
