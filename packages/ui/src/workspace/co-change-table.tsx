"use client";

import { Badge } from "../ui/badge";
import { EmptyState } from "../shared/empty-state";
import { VirtualizedTable } from "../shared/virtualized-table";
import type { WorkspaceCoChangeEntry } from "@repowise-dev/types/workspace";

interface CoChangeTableProps {
  coChanges: WorkspaceCoChangeEntry[];
  compact?: boolean;
}

// Column-priority hide classes, mirroring the shared ResponsiveTable scale:
// priority 2 hides below md (768px), priority 3 hides below lg (1024px). The
// source/target identity columns (priority 1) are always visible.
const HIDE_BELOW_MD = "max-md:hidden";
const HIDE_BELOW_LG = "max-lg:hidden";

/**
 * Cross-repo co-change list: one row per file pair that changed together.
 *
 * The body is virtualized (windowed `<tbody>`) so long co-change lists stay
 * cheap to render; below the wrapper's threshold every row renders, so the
 * common short list behaves exactly as a plain table.
 */
export function CoChangeTable({ coChanges, compact }: CoChangeTableProps) {
  if (coChanges.length === 0) {
    return (
      <EmptyState
        title="No cross-repo co-changes"
        description="No files in sibling repos have changed together yet."
      />
    );
  }

  const header = (
    <tr className="bg-[var(--color-bg-elevated)] text-[var(--color-text-tertiary)] text-xs uppercase tracking-wider">
      <th className="px-3 py-2 text-left font-medium">Source</th>
      <th className="px-3 py-2 text-left font-medium">Target</th>
      <th className={`px-3 py-2 text-left font-medium w-32 ${HIDE_BELOW_MD}`}>
        <span
          title="Share of the less-active file's recent work sessions that also touched the partner file (same author, recency-weighted). 100% would mean they always change together. It is not a verified dependency."
          className="cursor-help underline decoration-dotted underline-offset-2"
        >
          Strength
        </span>
      </th>
      {compact ? null : (
        <>
          <th className={`px-3 py-2 text-right font-medium ${HIDE_BELOW_LG}`}>Freq</th>
          <th className={`px-3 py-2 text-left font-medium ${HIDE_BELOW_LG}`}>Last</th>
        </>
      )}
    </tr>
  );

  const renderRow = (cc: WorkspaceCoChangeEntry) => (
    <tr className="border-t border-[var(--color-border-default)] hover:bg-[var(--color-bg-elevated)]">
      <td className="px-3 py-2 text-left min-w-[160px] max-w-[280px]">
        <div className="flex flex-col gap-0.5">
          <Badge variant="default" className="w-fit text-xs">{cc.source_repo}</Badge>
          <span className="text-xs font-mono text-[var(--color-text-secondary)] truncate block" title={cc.source_file}>
            {cc.source_file}
          </span>
        </div>
      </td>
      <td className="px-3 py-2 text-left min-w-[160px] max-w-[280px]">
        <div className="flex flex-col gap-0.5">
          <Badge variant="default" className="w-fit text-xs">{cc.target_repo}</Badge>
          <span className="text-xs font-mono text-[var(--color-text-secondary)] truncate block" title={cc.target_file}>
            {cc.target_file}
          </span>
        </div>
      </td>
      <td className={`px-3 py-2 text-left ${HIDE_BELOW_MD}`}>
        <div className="flex items-center gap-2 min-w-[90px]">
          <div className="h-1.5 flex-1 rounded-full bg-[var(--color-bg-inset)] overflow-hidden">
            <div
              className="h-full rounded-full bg-[var(--color-accent-primary)] transition-all"
              style={{ width: `${Math.min(Math.round(cc.strength * 100), 100)}%` }}
            />
          </div>
          <span className="text-xs text-[var(--color-text-tertiary)] tabular-nums w-8 text-right">
            {`${Math.round(cc.strength * 100)}%`}
          </span>
        </div>
      </td>
      {compact ? null : (
        <>
          <td className={`px-3 py-2 text-right text-xs text-[var(--color-text-secondary)] tabular-nums ${HIDE_BELOW_LG}`}>
            {`${cc.frequency}x`}
          </td>
          <td className={`px-3 py-2 text-left text-xs text-[var(--color-text-tertiary)] ${HIDE_BELOW_LG}`}>
            <span title={cc.last_date ? new Date(cc.last_date).toLocaleString() : undefined}>
              {cc.last_date ? new Date(cc.last_date).toLocaleDateString() : "—"}
            </span>
          </td>
        </>
      )}
    </tr>
  );

  return (
    <VirtualizedTable<WorkspaceCoChangeEntry>
      rows={coChanges}
      rowKey={(cc) => `${cc.source_repo}|${cc.source_file}|${cc.target_repo}|${cc.target_file}`}
      header={header}
      renderRow={renderRow}
      aria-label="Cross-repo co-changed files"
    />
  );
}
