"use client";

import { useState } from "react";
import { Button } from "../ui/button";
import { EmptyState } from "../shared/empty-state";
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
import type { DocPage } from "@repowise-dev/types/docs";

type Filter = "all" | "fresh" | "stale" | "outdated";

export interface FreshnessTableProps {
  /** Pages to render. Caller is responsible for fetching. */
  pages: DocPage[];
  /**
   * Invoked when the user clicks "Regenerate" on a row. Caller wires this
   * to whichever API client is appropriate; the table only manages the
   * per-row pending UI state. Omit to hide the regenerate button.
   */
  onRegenerate?: (pageId: string) => Promise<void>;
}

export function FreshnessTable({ pages, onRegenerate }: FreshnessTableProps) {
  const [filter, setFilter] = useState<Filter>("all");
  const [regenerating, setRegenerating] = useState<Set<string>>(new Set());

  const filtered =
    filter === "all"
      ? pages
      : pages.filter((p) => p.freshness_status === filter);

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
      <div role="tablist" aria-label="Freshness filter" className="flex items-center gap-1">
        {(["all", "fresh", "stale", "outdated"] as Filter[]).map((f) => {
          const count =
            f === "all"
              ? pages.length
              : pages.filter((p) => p.freshness_status === f).length;
          return (
            <button
              key={f}
              role="tab"
              aria-selected={filter === f}
              onClick={() => setFilter(f)}
              className={cn(
                "rounded-md px-3 py-1.5 text-xs font-medium transition-colors",
                filter === f
                  ? "bg-[var(--color-bg-surface)] text-[var(--color-text-primary)] shadow-sm"
                  : "text-[var(--color-text-secondary)] hover:text-[var(--color-text-primary)]",
              )}
            >
              {f === "all" ? "All" : statusLabel(f as FreshnessStatus)}
              <span className="ml-1 text-[var(--color-text-tertiary)]">({count})</span>
            </button>
          );
        })}
      </div>

      {filtered.length === 0 ? (
        <EmptyState title="No pages" description="No pages match this filter." />
      ) : (
        <div className="rounded-lg border border-[var(--color-border-default)] overflow-x-auto">
          <table className="w-full text-sm">
            <caption className="sr-only">Documentation freshness</caption>
            <thead className="sticky top-0 z-10 bg-[var(--color-bg-elevated)]">
              <tr className="border-b border-[var(--color-border-default)] bg-[var(--color-bg-elevated)]">
                <th scope="col" className="px-4 py-2.5 text-left text-xs font-medium text-[var(--color-text-tertiary)] uppercase tracking-wider">
                  Page
                </th>
                <th scope="col" className="px-4 py-2.5 text-left text-xs font-medium text-[var(--color-text-tertiary)] uppercase tracking-wider w-24">
                  Status
                </th>
                <th scope="col" className="px-4 py-2.5 text-left text-xs font-medium text-[var(--color-text-tertiary)] uppercase tracking-wider w-24">
                  Confidence
                </th>
                <th scope="col" className="px-4 py-2.5 text-left text-xs font-medium text-[var(--color-text-tertiary)] uppercase tracking-wider hidden md:table-cell">
                  Model
                </th>
                <th scope="col" className="px-4 py-2.5 text-left text-xs font-medium text-[var(--color-text-tertiary)] uppercase tracking-wider w-28 hidden md:table-cell">
                  Updated
                </th>
                <th scope="col" className="px-4 py-2.5 w-24">
                  <span className="sr-only">Action</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {filtered.map((page) => {
                const status = page.freshness_status as FreshnessStatus;
                return (
                  <tr
                    key={page.id}
                    className="border-b border-[var(--color-border-default)] hover:bg-[var(--color-bg-elevated)] transition-colors last:border-0"
                  >
                    <td className="px-4 py-2.5 font-mono text-xs text-[var(--color-text-primary)] min-w-[220px] max-w-[480px]">
                      <div className="truncate" title={page.target_path}>{page.target_path}</div>
                      <div className="truncate text-[var(--color-text-tertiary)]" title={page.page_type}>{page.page_type}</div>
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
                            ? "text-green-500"
                            : page.confidence >= 0.6
                              ? "text-yellow-500"
                              : "text-red-500",
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
                          {regenerating.has(page.id) ? "â€¦" : "Regenerate"}
                        </Button>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
