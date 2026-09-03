import * as React from "react";
import { formatDateTime, formatRelativeTime } from "../lib/format";

export interface ChangeStat {
  /** Pre-formatted figure, e.g. "8" or "10+". */
  value: string;
  /** Words after the figure, e.g. "commits". */
  label: string;
}

export interface ChangeLineProps {
  /**
   * ISO timestamp of the PREVIOUS index snapshot — the point the figures are
   * measured against, not the latest one.
   */
  since: string | null;
  stats: ChangeStat[];
}

/**
 * What moved between the last two indexes.
 *
 * This is the block the public repo landing page structurally cannot show:
 * that page serves a stranger arriving once, while this serves someone who was
 * here yesterday. Without it the local Overview is a slower copy of a page OSS
 * users cannot even reach.
 *
 * Measured against snapshots rather than against the last sync *timestamp*,
 * which is the version that does not work: a commit only appears in the index
 * once a sync has ingested it, and that same sync stamps `last_sync_at`, so
 * "commits newer than the last sync" is zero by construction. Decisions are
 * worse — they are created *by* the sync run. Snapshot deltas are the figures
 * that actually change.
 *
 * Renders nothing without two snapshots to compare, or when nothing moved: an
 * all-zeroes strip is noise pretending to be news.
 */
export function ChangeLine({ since, stats }: ChangeLineProps) {
  if (!since || stats.length === 0) return null;

  return (
    <div className="flex flex-wrap items-baseline gap-x-4 gap-y-1 border-y border-[var(--color-border-default)] py-2.5 text-xs text-[var(--color-text-secondary)]">
      <span
        className="font-mono text-[10px] uppercase tracking-[0.12em] text-[var(--color-text-tertiary)]"
        title={formatDateTime(since)}
      >
        Since the previous index, {formatRelativeTime(since)}
      </span>
      {stats.map((s) => (
        <span key={s.label}>
          <span className="font-mono font-semibold tabular-nums text-[var(--color-text-primary)]">
            {s.value}
          </span>{" "}
          {s.label}
        </span>
      ))}
    </div>
  );
}
