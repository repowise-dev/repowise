"use client";

import { Badge } from "../ui/badge";
import { EmptyState } from "../shared/empty-state";
import { VirtualizedTable } from "../shared/virtualized-table";
import { clickableRowProps, CLICKABLE_ROW_CLS } from "../shared/responsive-table";
import { cn } from "../lib/cn";
import { ChevronRight } from "lucide-react";

export interface RepoPairSummary {
  id: string; // "repo1↔repo2"
  repo1: string;
  repo2: string;
  filePairCount: number;
  maxStrength: number;
  lastDate: string;
}

interface RepoPairTableProps {
  repoPairs: RepoPairSummary[];
  onSelectPair?: (id: string) => void;
  selectedPairId?: string | null;
}

// Column-priority hide classes, mirroring the shared ResponsiveTable scale:
// priority 2 hides below md (768px), priority 3 hides below lg (1024px). The
// pair identity, max-strength and action columns (priority 1) stay visible.
const HIDE_BELOW_MD = "max-md:hidden";
const HIDE_BELOW_LG = "max-lg:hidden";

/**
 * Cross-repo pair summary: one row per repository pair, click to drill in.
 *
 * The body is virtualized (windowed `<tbody>`) so long pair lists stay cheap to
 * render; below the wrapper's threshold every row renders, so the common short
 * list behaves exactly as a plain table.
 */
export function RepoPairTable({ repoPairs, onSelectPair, selectedPairId }: RepoPairTableProps) {
  if (repoPairs.length === 0) {
    return (
      <EmptyState
        title="No repository pairs"
        description="No cross-repository co-changes found."
      />
    );
  }

  const header = (
    <tr className="bg-[var(--color-bg-elevated)] text-[var(--color-text-tertiary)] text-xs uppercase tracking-wider">
      <th className="px-3 py-2 text-left font-medium">Repository Pair</th>
      <th className={`px-3 py-2 text-right font-medium ${HIDE_BELOW_MD}`}>File Pairs</th>
      <th className="px-3 py-2 text-left font-medium w-32">Max Strength</th>
      <th className={`px-3 py-2 text-right font-medium ${HIDE_BELOW_LG}`}>Latest Activity</th>
      {onSelectPair ? <th className="px-3 py-2 text-right font-medium" /> : null}
    </tr>
  );

  const renderRow = (p: RepoPairSummary) => {
    const onClick = onSelectPair ? () => onSelectPair(p.id) : undefined;
    const isSelected = selectedPairId != null && selectedPairId === p.id;
    return (
      <tr
        className={cn(
          "border-t border-[var(--color-border-default)] hover:bg-[var(--color-bg-elevated)]",
          isSelected && "bg-[var(--color-accent-muted)]/30",
          onClick && CLICKABLE_ROW_CLS,
        )}
        {...(onClick ? clickableRowProps(onClick) : {})}
      >
        <td className="px-3 py-2 text-left">
          <div className="flex items-center gap-2">
            <Badge variant="default" className="text-xs">{p.repo1}</Badge>
            <span className="text-[var(--color-text-tertiary)] text-xs">↔</span>
            <Badge variant="default" className="text-xs">{p.repo2}</Badge>
          </div>
        </td>
        <td className={`px-3 py-2 text-right text-xs text-[var(--color-text-secondary)] tabular-nums ${HIDE_BELOW_MD}`}>
          {p.filePairCount}
        </td>
        <td className="px-3 py-2 text-left">
          <div className="flex items-center gap-2 min-w-[90px]">
            <div className="h-1.5 flex-1 rounded-full bg-[var(--color-bg-inset)] overflow-hidden">
              <div
                className="h-full rounded-full bg-[var(--color-accent-primary)] transition-all"
                style={{ width: `${Math.min(Math.round(p.maxStrength * 10), 100)}%` }}
              />
            </div>
            <span className="text-xs text-[var(--color-text-tertiary)] tabular-nums w-8 text-right">
              {Math.round(p.maxStrength * 10) / 10}
            </span>
          </div>
        </td>
        <td className={`px-3 py-2 text-right text-xs text-[var(--color-text-tertiary)] ${HIDE_BELOW_LG}`}>
          <span title={p.lastDate ? new Date(p.lastDate).toLocaleString() : undefined}>
            {p.lastDate ? new Date(p.lastDate).toLocaleDateString() : "—"}
          </span>
        </td>
        {onSelectPair ? (
          <td className="px-3 py-2 text-right">
            <ChevronRight className="h-4 w-4 text-[var(--color-text-tertiary)] inline-block" />
          </td>
        ) : null}
      </tr>
    );
  };

  return (
    <VirtualizedTable<RepoPairSummary>
      rows={repoPairs}
      rowKey={(p) => p.id}
      header={header}
      renderRow={renderRow}
      aria-label="Cross-repo pairs"
    />
  );
}
