import { ArrowUpRight } from "lucide-react";

import { coverageBand } from "./tokens";

export interface UntestedHotspotEntry {
  file_path: string;
  line_coverage_pct: number | null;
  dependents_count?: number;
  commit_count_90d?: number | null;
  health_score?: number;
}

export interface UntestedHotspotWarningProps {
  entries: UntestedHotspotEntry[];
  limit?: number;
  /** Open a file's coverage page. When set, rows become clickable. */
  onSelect?: ((filePath: string) => void) | undefined;
}

/**
 * The files where a coverage gap actually costs something, as hairline rows.
 *
 * It used to be a tinted warning panel with its own border, icon, heading and
 * description, which is four pieces of chrome around a list the section header
 * now names. The colour moved onto the one figure that carries a band, the
 * coverage percentage, so a file at 0% reads louder than one at 28% instead of
 * every row reading equally alarming because the panel behind them is amber.
 */
export function UntestedHotspotWarning({
  entries,
  limit = 6,
  onSelect,
}: UntestedHotspotWarningProps) {
  if (entries.length === 0) return null;
  const shown = entries.slice(0, limit);

  return (
    <div className="flex flex-col">
      <ul className="border-t border-[var(--color-border-default)]">
        {shown.map((e) => {
          const body = (
            <>
              <span className="min-w-0 flex-1 truncate font-mono text-xs text-[var(--color-text-primary)]">
                {e.file_path}
              </span>
              <span className="shrink-0 tabular-nums text-xs text-[var(--color-text-tertiary)]">
                {e.dependents_count != null && `${e.dependents_count} dependents`}
                {e.dependents_count != null &&
                  e.commit_count_90d != null &&
                  e.commit_count_90d > 0 &&
                  " · "}
                {e.commit_count_90d != null &&
                  e.commit_count_90d > 0 &&
                  `${e.commit_count_90d} commits in 90d`}
              </span>
              <span
                className="w-[4.5rem] shrink-0 text-right text-xs font-semibold tabular-nums"
                style={{
                  color:
                    e.line_coverage_pct == null
                      ? "var(--color-text-tertiary)"
                      : coverageBand(e.line_coverage_pct).color,
                }}
              >
                {e.line_coverage_pct == null
                  ? "no data"
                  : `${e.line_coverage_pct.toFixed(0)}%`}
              </span>
            </>
          );

          return (
            <li
              key={e.file_path}
              className="border-b border-[var(--color-border-default)]"
            >
              {onSelect ? (
                <button
                  type="button"
                  onClick={() => onSelect(e.file_path)}
                  className="group flex w-full items-baseline gap-x-4 py-2.5 text-left hover:bg-[var(--color-bg-elevated)]"
                >
                  {body}
                  <ArrowUpRight className="h-3 w-3 shrink-0 self-center text-[var(--color-text-tertiary)] group-hover:text-[var(--color-accent-primary)]" />
                </button>
              ) : (
                <div className="flex w-full items-baseline gap-x-4 py-2.5">{body}</div>
              )}
            </li>
          );
        })}
      </ul>
      {entries.length > limit && (
        <p className="mt-2 text-xs text-[var(--color-text-tertiary)]">
          {entries.length - limit} more sit in the file table below.
        </p>
      )}
    </div>
  );
}
