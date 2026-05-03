import { formatNumber } from "../lib/format";
import type { DeadCodeSummary } from "@repowise-dev/types/dead-code";

export interface SummaryBarProps {
  /** Summary rollup; caller fetches. */
  summary: DeadCodeSummary;
}

export function SummaryBar({ summary }: SummaryBarProps) {
  return (
    <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
      <div className="rounded-lg border border-[var(--color-border-default)] bg-[var(--color-bg-surface)] p-4">
        <p className="text-xs font-medium text-[var(--color-text-tertiary)] uppercase tracking-wider mb-1">
          Total Findings
        </p>
        <p className="text-2xl font-bold text-[var(--color-text-primary)] tabular-nums">
          {formatNumber(summary.total_findings)}
        </p>
      </div>

      <div className="rounded-lg border border-[var(--color-border-default)] bg-[var(--color-bg-surface)] p-4">
        <p className="text-xs font-medium text-[var(--color-text-tertiary)] uppercase tracking-wider mb-1">
          Deletable Lines
        </p>
        <p className="text-2xl font-bold text-red-500 tabular-nums">
          {formatNumber(summary.deletable_lines)}
        </p>
      </div>

      <div className="rounded-lg border border-[var(--color-border-default)] bg-[var(--color-bg-surface)] p-4">
        <p className="text-xs font-medium text-[var(--color-text-tertiary)] uppercase tracking-wider mb-2">
          By Kind
        </p>
        <div className="space-y-1">
          {Object.entries(summary.by_kind).map(([kind, count]) => (
            <div key={kind} className="flex items-center justify-between text-xs">
              <span className="text-[var(--color-text-secondary)] truncate">{kind.replace(/_/g, " ")}</span>
              <span className="text-[var(--color-text-primary)] tabular-nums font-medium ml-2">
                {formatNumber(count)}
              </span>
            </div>
          ))}
        </div>
      </div>

      <div className="rounded-lg border border-[var(--color-border-default)] bg-[var(--color-bg-surface)] p-4">
        <p className="text-xs font-medium text-[var(--color-text-tertiary)] uppercase tracking-wider mb-2">
          Confidence
        </p>
        <div className="space-y-1">
          {Object.entries(summary.confidence_summary).map(([level, count]) => (
            <div key={level} className="flex items-center justify-between text-xs">
              <span className="text-[var(--color-text-secondary)]">{level}</span>
              <span className="text-[var(--color-text-primary)] tabular-nums font-medium">
                {formatNumber(count)}
              </span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
