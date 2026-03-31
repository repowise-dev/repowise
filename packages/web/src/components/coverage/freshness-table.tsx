"use client";

import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { EmptyState } from "@/components/shared/empty-state";
import { regeneratePage } from "@/lib/api/pages";
import { statusBadgeClasses, statusLabel } from "@/lib/utils/confidence";
import { truncatePath, formatConfidence, formatRelativeTime } from "@/lib/utils/format";
import { cn } from "@/lib/utils/cn";
import type { PageResponse } from "@/lib/api/types";
import type { FreshnessStatus } from "@/lib/utils/confidence";

type Filter = "all" | FreshnessStatus;

interface FreshnessTableProps {
  pages: PageResponse[];
}

export function FreshnessTable({ pages }: FreshnessTableProps) {
  const [filter, setFilter] = useState<Filter>("all");
  const [regenerating, setRegenerating] = useState<Set<string>>(new Set());

  const filtered = filter === "all" ? pages : pages.filter((p) => p.freshness_status === filter);

  const handleRegenerate = async (pageId: string) => {
    setRegenerating((prev) => new Set(prev).add(pageId));
    try {
      await regeneratePage(pageId);
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
      {/* Filter tabs */}
      <div className="flex items-center gap-1">
        {(["all", "fresh", "stale", "outdated"] as Filter[]).map((f) => {
          const count = f === "all" ? pages.length : pages.filter((p) => p.freshness_status === f).length;
          return (
            <button
              key={f}
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
            <thead>
              <tr className="border-b border-[var(--color-border-default)] bg-[var(--color-bg-elevated)]">
                <th className="px-4 py-2.5 text-left text-xs font-medium text-[var(--color-text-tertiary)] uppercase tracking-wider">
                  Page
                </th>
                <th className="px-4 py-2.5 text-left text-xs font-medium text-[var(--color-text-tertiary)] uppercase tracking-wider w-24">
                  Status
                </th>
                <th className="px-4 py-2.5 text-left text-xs font-medium text-[var(--color-text-tertiary)] uppercase tracking-wider w-24">
                  Confidence
                </th>
                <th className="px-4 py-2.5 text-left text-xs font-medium text-[var(--color-text-tertiary)] uppercase tracking-wider">
                  Model
                </th>
                <th className="px-4 py-2.5 text-left text-xs font-medium text-[var(--color-text-tertiary)] uppercase tracking-wider w-28">
                  Updated
                </th>
                <th className="px-4 py-2.5 w-24" />
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
                    <td className="px-4 py-2.5 font-mono text-xs text-[var(--color-text-primary)]" style={{ maxWidth: 0 }}>
                      <div className="truncate" title={page.target_path}>{page.target_path}</div>
                      <div className="text-[var(--color-text-tertiary)]">{page.page_type}</div>
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
                    <td className="px-4 py-2.5 text-xs text-[var(--color-text-tertiary)]">
                      {page.model_name}
                    </td>
                    <td className="px-4 py-2.5 text-xs text-[var(--color-text-tertiary)]">
                      {formatRelativeTime(page.updated_at)}
                    </td>
                    <td className="px-4 py-2.5">
                      <Button
                        size="sm"
                        variant="ghost"
                        disabled={regenerating.has(page.id)}
                        onClick={() => handleRegenerate(page.id)}
                        className="h-6 px-2 text-xs"
                      >
                        {regenerating.has(page.id) ? "…" : "Regenerate"}
                      </Button>
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
