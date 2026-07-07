import { AlertTriangle, ArrowUpRight } from "lucide-react";

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

export function UntestedHotspotWarning({
  entries,
  limit = 5,
  onSelect,
}: UntestedHotspotWarningProps) {
  if (entries.length === 0) return null;
  const shown = entries.slice(0, limit);
  return (
    <div className="rounded-lg border border-[var(--color-warning)]/40 bg-[var(--color-warning)]/5 p-4">
      <div className="flex items-start gap-2 mb-2">
        <AlertTriangle className="h-4 w-4 text-[var(--color-warning)] mt-0.5" />
        <div>
          <h3 className="text-sm font-semibold text-[var(--color-text-primary)]">
            Untested hotspots
          </h3>
          <p className="text-xs text-[var(--color-text-secondary)]">
            High-churn, centrally depended-on files with little or no test coverage.
          </p>
        </div>
      </div>
      <ul className="space-y-0.5">
        {shown.map((e) => {
          const meta = (
            <span className="text-xs text-[var(--color-text-tertiary)] tabular-nums">
              {e.line_coverage_pct == null
                ? "no coverage data"
                : `${e.line_coverage_pct.toFixed(0)}% covered`}
              {e.dependents_count != null && ` · ${e.dependents_count} dependents`}
              {e.commit_count_90d != null &&
                e.commit_count_90d > 0 &&
                ` · ${e.commit_count_90d} commits/90d`}
            </span>
          );
          if (onSelect) {
            return (
              <li key={e.file_path}>
                <button
                  type="button"
                  onClick={() => onSelect(e.file_path)}
                  className="group flex w-full flex-wrap items-baseline gap-x-3 rounded-md px-2 py-1 text-left text-sm hover:bg-[var(--color-warning)]/10"
                >
                  <span className="inline-flex items-center gap-1 font-mono text-[var(--color-text-primary)] truncate">
                    <span className="truncate">{e.file_path}</span>
                    <ArrowUpRight className="h-3 w-3 shrink-0 text-[var(--color-text-tertiary)] group-hover:text-[var(--color-warning)]" />
                  </span>
                  {meta}
                </button>
              </li>
            );
          }
          return (
            <li
              key={e.file_path}
              className="flex flex-wrap items-baseline gap-x-3 px-2 py-1 text-sm"
            >
              <span className="font-mono text-[var(--color-text-primary)] truncate">
                {e.file_path}
              </span>
              {meta}
            </li>
          );
        })}
      </ul>
      {entries.length > limit && (
        <p className="text-xs text-[var(--color-text-tertiary)] mt-2">
          + {entries.length - limit} more
        </p>
      )}
    </div>
  );
}
