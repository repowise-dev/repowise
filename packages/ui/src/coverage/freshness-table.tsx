"use client";

import { useMemo, useState, type ReactNode } from "react";
import { Button } from "../ui/button";
import { EmptyState } from "../shared/empty-state";
import { VirtualizedTable } from "../shared/virtualized-table";
import { cn } from "../lib/cn";
import {
  statusBadgeClasses,
  statusLabel,
  type FreshnessStatus,
} from "../lib/confidence";
import {
  formatConfidence,
  formatRelativeTime,
} from "../lib/format";
import {
  getPageTypeIcon,
  getPageTypeLabel,
  isDeterministicPage,
} from "../lib/page-types";
import type { DocPage } from "@repowise-dev/types/docs";

// "auto" is a provenance filter (template pages), orthogonal to freshness — it
// lets a user jump to the pages worth upgrading and select them in one place.
type Filter = "all" | "fresh" | "stale" | "outdated" | "auto";

export interface FreshnessTableProps {
  /** Pages to render. Caller is responsible for fetching. */
  pages: DocPage[];
  /**
   * Invoked when the user clicks "Regenerate" on a row. Caller wires this
   * to whichever API client is appropriate; the table only manages the
   * per-row pending UI state. Omit to hide the regenerate button.
   */
  onRegenerate?: (pageId: string) => Promise<void>;
  /**
   * When true, each row gets a checkbox and a "select all template pages"
   * affordance is shown, for launching a bulk generate. Selection state is
   * owned by the caller (so the cost estimate lives with the data wiring).
   */
  selectable?: boolean;
  selectedIds?: Set<string>;
  onToggleSelect?: (pageId: string) => void;
  /** Called with every template (auto) page id — powers "select all template
   *  pages". */
  onSelectTemplates?: (templateIds: string[]) => void;
  onClearSelection?: () => void;
  /** Rendered above the table — typically the running count + cost + a
   *  "Write selected" action the caller builds from the selection. */
  toolbar?: ReactNode;
}

export function FreshnessTable({
  pages,
  onRegenerate,
  selectable = false,
  selectedIds,
  onToggleSelect,
  onSelectTemplates,
  onClearSelection,
  toolbar,
}: FreshnessTableProps) {
  const [filter, setFilter] = useState<Filter>("all");
  const [regenerating, setRegenerating] = useState<Set<string>>(new Set());

  // Template (auto) page ids power the "select all template pages" affordance.
  const templateIds = useMemo(
    () => pages.filter((p) => isDeterministicPage(p)).map((p) => p.id),
    [pages],
  );
  const selectedCount = selectedIds?.size ?? 0;
  const allTemplatesSelected =
    templateIds.length > 0 && templateIds.every((id) => selectedIds?.has(id));

  // The filter is a set of discrete status toggle buttons, not a free-text
  // search input, so there are no rapid keystroke updates to debounce — each
  // click is a single discrete state change. We only need to memoize the
  // derived data so the four count scans and the filter scan don't re-run on
  // unrelated re-renders (e.g. per-row regenerate state changes).
  const counts = useMemo<Record<Filter, number>>(() => {
    let fresh = 0;
    let stale = 0;
    let outdated = 0;
    let auto = 0;
    for (const p of pages) {
      if (p.freshness_status === "fresh") fresh += 1;
      else if (p.freshness_status === "stale") stale += 1;
      else if (p.freshness_status === "outdated") outdated += 1;
      // Provenance is independent of freshness, so count it separately.
      if (isDeterministicPage(p)) auto += 1;
    }
    return { all: pages.length, fresh, stale, outdated, auto };
  }, [pages]);

  const filtered = useMemo(() => {
    if (filter === "all") return pages;
    if (filter === "auto") return pages.filter((p) => isDeterministicPage(p));
    return pages.filter((p) => p.freshness_status === filter);
  }, [pages, filter]);

  const handleRegenerate = async (pageId: string) => {
    if (!onRegenerate) return;
    setRegenerating((prev) => new Set(prev).add(pageId));
    try {
      await onRegenerate(pageId);
    } finally {
      setRegenerating((prev) => {
        const next = new Set(prev);
        next.delete(pageId);
        return next;
      });
    }
  };

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div role="tablist" aria-label="Filter pages" className="flex flex-wrap items-center gap-1">
          {(["all", "fresh", "stale", "outdated", "auto"] as Filter[]).map((f) => {
            const count = counts[f];
            const label =
              f === "all" ? "All" : f === "auto" ? "Auto" : statusLabel(f as FreshnessStatus);
            return (
              <button
                key={f}
                role="tab"
                aria-selected={filter === f}
                onClick={() => setFilter(f)}
                title={
                  f === "auto"
                    ? "Pages generated from code structure (upgrade candidates)"
                    : undefined
                }
                className={cn(
                  "rounded-md px-3 py-1.5 text-xs font-medium transition-colors",
                  filter === f
                    ? "bg-[var(--color-bg-surface)] text-[var(--color-text-primary)] shadow-sm"
                    : "text-[var(--color-text-secondary)] hover:text-[var(--color-text-primary)]",
                )}
              >
                {label}
                <span className="ml-1 text-[var(--color-text-tertiary)]">({count})</span>
              </button>
            );
          })}
        </div>

        {selectable && templateIds.length > 0 && (
          <div className="flex items-center gap-3 text-xs">
            <button
              type="button"
              onClick={() =>
                allTemplatesSelected
                  ? onClearSelection?.()
                  : onSelectTemplates?.(templateIds)
              }
              className="font-medium text-[var(--color-accent-primary)] hover:text-[var(--color-accent-hover)]"
            >
              {allTemplatesSelected
                ? "Clear selection"
                : `Select all ${templateIds.length} template pages`}
            </button>
            {selectedCount > 0 && !allTemplatesSelected && onClearSelection && (
              <button
                type="button"
                onClick={() => onClearSelection()}
                className="text-[var(--color-text-tertiary)] hover:text-[var(--color-text-secondary)]"
              >
                Clear
              </button>
            )}
          </div>
        )}
      </div>

      {selectable && toolbar}

      {filtered.length === 0 ? (
        <EmptyState title="No pages" description="No pages match this filter." />
      ) : (
        <VirtualizedTable<DocPage>
          rows={filtered}
          rowKey={(page) => page.id}
          estimateRowHeight={56}
          aria-label="Documentation freshness"
          className="border border-[var(--color-border-default)]"
          tableClassName="w-full text-sm"
          headerClassName="bg-[var(--color-bg-surface)]"
          header={
            <tr className="border-b border-[var(--color-border-default)] bg-[var(--color-bg-surface)]">
              {selectable && (
                <th scope="col" className="w-10 px-4 py-2.5">
                  <span className="sr-only">Select</span>
                </th>
              )}
              <th scope="col" className="px-4 py-2.5 text-left text-[11px] font-medium text-[var(--color-text-tertiary)] uppercase tracking-wider">
                Page
              </th>
              <th scope="col" className="px-4 py-2.5 text-left text-[11px] font-medium text-[var(--color-text-tertiary)] uppercase tracking-wider w-24">
                Status
              </th>
              <th scope="col" className="px-4 py-2.5 text-left text-[11px] font-medium text-[var(--color-text-tertiary)] uppercase tracking-wider w-24">
                Confidence
              </th>
              <th scope="col" className="px-4 py-2.5 text-left text-[11px] font-medium text-[var(--color-text-tertiary)] uppercase tracking-wider hidden md:table-cell">
                Model
              </th>
              <th scope="col" className="px-4 py-2.5 text-left text-[11px] font-medium text-[var(--color-text-tertiary)] uppercase tracking-wider w-28 hidden md:table-cell">
                Updated
              </th>
              <th scope="col" className="px-4 py-2.5 w-24">
                <span className="sr-only">Action</span>
              </th>
            </tr>
          }
          renderRow={(page) => {
            const status = page.freshness_status as FreshnessStatus;
            return (
              <tr
                className={cn(
                  "group border-b border-[var(--color-table-divider)] hover:bg-[var(--color-bg-elevated)] transition-colors last:border-0",
                  selectable && selectedIds?.has(page.id) && "bg-[var(--color-accent-muted)]",
                )}
              >
                {selectable && (
                  <td className="px-4 py-2.5">
                    <input
                      type="checkbox"
                      checked={selectedIds?.has(page.id) ?? false}
                      onChange={() => onToggleSelect?.(page.id)}
                      className="accent-[var(--color-accent-fill)]"
                      aria-label={`Select ${page.target_path}`}
                    />
                  </td>
                )}
                <td className="px-4 py-2.5 font-mono text-xs text-[var(--color-text-primary)] min-w-[220px] max-w-[480px]">
                  <div className="truncate group-hover:underline underline-offset-2" title={page.target_path}>{page.target_path}</div>
                  {(() => { const TypeIcon = getPageTypeIcon(page.page_type); return (
                    <div className="flex items-center gap-1 truncate text-[var(--color-text-tertiary)]" title={page.page_type}>
                      <TypeIcon className="h-3 w-3 shrink-0" />
                      <span>{getPageTypeLabel(page.page_type)}</span>
                    </div>
                  ); })()}
                </td>
                <td className="px-4 py-2.5">
                  <span
                    className={cn(
                      "inline-flex items-center rounded border px-2 py-0.5 text-xs font-medium",
                      statusBadgeClasses(status),
                    )}
                  >
                    {statusLabel(status)}
                  </span>
                </td>
                <td className="px-4 py-2.5 text-xs tabular-nums">
                  <span
                    className={cn(
                      page.confidence >= 0.8
                        ? "text-[var(--color-success)]"
                        : page.confidence >= 0.6
                          ? "text-[var(--color-warning)]"
                          : "text-[var(--color-error)]",
                    )}
                  >
                    {formatConfidence(page.confidence)}
                  </span>
                </td>
                <td className="px-4 py-2.5 text-xs text-[var(--color-text-tertiary)] max-w-[200px] hidden md:table-cell">
                  <span className="block truncate" title={page.model_name}>{page.model_name}</span>
                </td>
                <td
                  className="px-4 py-2.5 text-xs text-[var(--color-text-tertiary)] hidden md:table-cell"
                  title={new Date(page.updated_at).toLocaleString()}
                >
                  {formatRelativeTime(page.updated_at)}
                </td>
                <td className="px-4 py-2.5">
                  {onRegenerate && (
                    <Button
                      size="sm"
                      variant="ghost"
                      disabled={regenerating.has(page.id)}
                      onClick={() => handleRegenerate(page.id)}
                      className="h-6 px-2 text-xs"
                    >
                      {regenerating.has(page.id) ? "…" : "Regenerate"}
                    </Button>
                  )}
                </td>
              </tr>
            );
          }}
        />
      )}
    </div>
  );
}
