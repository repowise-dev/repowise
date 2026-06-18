import { formatNumber } from "../lib/format";
import { MetricCard } from "../shared/metric-card";
import type { DeadCodeSummary } from "@repowise-dev/types/dead-code";

export interface SummaryBarProps {
  /** Summary rollup; caller fetches. */
  summary: DeadCodeSummary;
}

/** Compact list rendered inside a MetricCard value slot (By Kind / Confidence). */
function CountList({ entries }: { entries: [string, number][] }) {
  return (
    <div className="space-y-1 text-xs font-normal">
      {entries.map(([label, count]) => (
        <div key={label} className="flex items-center justify-between gap-2">
          <span className="truncate text-[var(--color-text-secondary)]">
            {label.replace(/_/g, " ")}
          </span>
          <span className="ml-2 font-medium tabular-nums text-[var(--color-text-primary)]">
            {formatNumber(count)}
          </span>
        </div>
      ))}
    </div>
  );
}

export function SummaryBar({ summary }: SummaryBarProps) {
  return (
    <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
      <MetricCard
        label="Total Findings"
        value={formatNumber(summary.total_findings)}
      />
      <MetricCard
        label="Candidate Lines"
        value={
          <span className="text-[var(--color-warning)]">
            {formatNumber(summary.deletable_lines)}
          </span>
        }
      />
      <MetricCard
        label="By Kind"
        value={<CountList entries={Object.entries(summary.by_kind)} />}
      />
      <MetricCard
        label="Confidence"
        value={<CountList entries={Object.entries(summary.confidence_summary)} />}
      />
    </div>
  );
}
