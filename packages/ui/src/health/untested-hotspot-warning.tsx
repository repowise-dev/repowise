import { AlertTriangle } from "lucide-react";

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
}

export function UntestedHotspotWarning({
  entries,
  limit = 5,
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
      <ul className="space-y-1.5">
        {shown.map((e) => (
          <li
            key={e.file_path}
            className="flex flex-wrap items-baseline gap-x-3 text-sm"
          >
            <span className="font-mono text-[var(--color-text-primary)] truncate">
              {e.file_path}
            </span>
            <span className="text-xs text-[var(--color-text-tertiary)] tabular-nums">
              {e.line_coverage_pct == null
                ? "no coverage data"
                : `${e.line_coverage_pct.toFixed(0)}% covered`}
              {e.dependents_count != null && ` · ${e.dependents_count} dependents`}
              {e.commit_count_90d != null &&
                e.commit_count_90d > 0 &&
                ` · ${e.commit_count_90d} commits/90d`}
            </span>
          </li>
        ))}
      </ul>
      {entries.length > limit && (
        <p className="text-xs text-[var(--color-text-tertiary)] mt-2">
          + {entries.length - limit} more
        </p>
      )}
    </div>
  );
}
