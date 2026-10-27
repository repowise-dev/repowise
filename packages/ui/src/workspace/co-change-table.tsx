"use client";

import * as React from "react";
import { EmptyState } from "../shared/empty-state";
import { InfoTip } from "../shared/info-tip";
import { clickableRowProps, CLICKABLE_ROW_CLS } from "../shared/responsive-table";
import { FilePath } from "../shared/file-path";
import { cn } from "../lib/cn";
import { formatDate, formatDateTime } from "../lib/format";
import { PAIR_MICRO as MICRO } from "../coupling/pair-drawer-parts";
import { SESSIONS_DEFINITION, STRENGTH_DEFINITION } from "./co-change-facts";
import type { WorkspaceCoChangeEntry } from "@repowise-dev/types/workspace";

interface CoChangeTableProps {
  coChanges: WorkspaceCoChangeEntry[];
  /** Drops the sessions and date columns, for previews. */
  compact?: boolean;
  /** Makes rows open the pair; omitted, rows are plain. */
  onSelect?: (cc: WorkspaceCoChangeEntry) => void;
  /** `coChangeKey` of the open pair, marked as selected. */
  selectedKey?: string | null;
}

/**
 * Stable identity for one file pair, used for row keys and the `?cc=` param.
 * Order-independent, so a row flipped to lead with a searched file keeps it.
 */
export function coChangeKey(cc: WorkspaceCoChangeEntry): string {
  const a = `${cc.source_repo}:${cc.source_file}`;
  const b = `${cc.target_repo}:${cc.target_file}`;
  return a < b ? `${a}|${b}` : `${b}|${a}`;
}


// Priority 2 hides below md, matching the shared ResponsiveTable scale. The two
// files and the strength stay at every width.
const HIDE_BELOW_MD = "max-md:hidden";

/**
 * A repository as a quiet micro-label over its path. In a row the file name
 * leads and the directory clips; with `wrap`, where the exact file must be
 * legible, the whole path wraps only after a "/".
 */
export function RepoPath({ repo, path, wrap = false }: { repo: string; path: string; wrap?: boolean }) {
  return (
    <div className="min-w-0">
      <span className={cn(MICRO, "block")}>{repo}</span>
      <FilePath
        path={path}
        mode={wrap ? "full" : "name-first"}
        className="leading-5 text-[var(--color-text-primary)]"
      />
    </div>
  );
}

interface RowProps {
  cc: WorkspaceCoChangeEntry;
  compact: boolean;
  selected: boolean;
  onSelect: ((cc: WorkspaceCoChangeEntry) => void) | undefined;
}

/** Memoised so opening a pair or narrowing the list re-renders only the rows that changed. */
const CoChangeRow = React.memo(function CoChangeRow({ cc, compact, selected, onSelect }: RowProps) {
  const interactive = onSelect !== undefined;
  return (
    <tr
      className={cn(
        "border-t border-[var(--color-border-default)] align-top",
        interactive && "hover:bg-[var(--color-bg-elevated)]",
        selected && "bg-[var(--color-selection-wash)]",
        interactive && CLICKABLE_ROW_CLS,
      )}
      aria-current={selected ? "true" : undefined}
      {...(interactive ? clickableRowProps(() => onSelect(cc)) : {})}
    >
      <td className="px-3 py-2.5 text-left">
        <RepoPath repo={cc.source_repo} path={cc.source_file} />
      </td>
      <td className="px-3 py-2.5 text-left">
        <RepoPath repo={cc.target_repo} path={cc.target_file} />
      </td>
      <td className="px-3 py-2.5 text-right align-middle font-mono text-xs tabular-nums text-[var(--color-text-primary)]">
        {Math.round(cc.strength * 100)}%
      </td>
      {compact ? null : (
        <>
          <td
            className={`px-3 py-2.5 text-right align-middle font-mono text-xs tabular-nums text-[var(--color-text-secondary)] ${HIDE_BELOW_MD}`}
          >
            {cc.frequency}
          </td>
          <td
            className={`whitespace-nowrap px-3 py-2.5 text-right align-middle font-mono text-xs tabular-nums text-[var(--color-text-tertiary)] ${HIDE_BELOW_MD}`}
          >
            {/* formatDate, not toLocaleDateString(): the bare call resolves
                the ambient locale, so Node and the browser can render the same
                date differently and hydration fails. */}
            <span title={cc.last_date ? formatDateTime(cc.last_date) : undefined}>
              {cc.last_date ? formatDate(cc.last_date) : "unknown"}
            </span>
          </td>
        </>
      )}
    </tr>
  );
});

/**
 * Cross-repo co-change list: one row per file pair that changed together.
 *
 * A plain table in the page flow rather than a windowed inner scroller: the
 * miner stores at most 200 pairs and the endpoint serves at most 500, and a
 * fixed-height scroller left the page empty below it. Rows are memoised
 * instead. If the endpoint ever serves thousands, window it again.
 *
 * No strength bar: in a list sorted by strength, neighbouring rows differ by a
 * point or two, so a bar per row repeats one shape. The figure is exact and
 * aligns.
 */
export function CoChangeTable({ coChanges, compact = false, onSelect, selectedKey }: CoChangeTableProps) {
  if (coChanges.length === 0) {
    return (
      <EmptyState
        title="No cross-repo co-changes"
        description="No files in sibling repos have changed together yet."
      />
    );
  }

  return (
    <div className="overflow-x-auto">
      <table className="w-full border-collapse" aria-label="Cross-repo co-changed files">
        <thead>
          <tr className="border-b border-[var(--color-border-default)]">
            <th className={cn(MICRO, "px-3 py-2 text-left font-normal")}>File</th>
            <th className={cn(MICRO, "px-3 py-2 text-left font-normal")}>Changes with</th>
            <th className={cn(MICRO, "px-3 py-2 text-right font-normal")}>
              <span className="inline-flex items-center gap-1">
                Strength
                <InfoTip content={STRENGTH_DEFINITION} label="What strength means" />
              </span>
            </th>
            {compact ? null : (
              <>
                <th className={cn(MICRO, "px-3 py-2 text-right font-normal", HIDE_BELOW_MD)}>
                  <span className="inline-flex items-center gap-1">
                    Sessions
                    <InfoTip content={SESSIONS_DEFINITION} label="What a shared session is" />
                  </span>
                </th>
                <th className={cn(MICRO, "px-3 py-2 text-right font-normal", HIDE_BELOW_MD)}>
                  Last together
                </th>
              </>
            )}
          </tr>
        </thead>
        <tbody>
          {coChanges.map((cc) => {
            const key = coChangeKey(cc);
            return (
              <CoChangeRow
                key={key}
                cc={cc}
                compact={compact}
                selected={selectedKey === key}
                onSelect={onSelect}
              />
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
