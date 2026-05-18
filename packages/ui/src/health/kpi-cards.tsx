import { formatNumber } from "../lib/format";

export interface HealthSummary {
  file_count: number;
  average_health: number;
  worst_performer_path: string | null;
  worst_performer_score: number | null;
  open_findings: number;
}

export interface HealthKpiCardsProps {
  summary: HealthSummary;
}

function scoreColor(score: number | null | undefined): string {
  if (score == null) return "text-[var(--color-text-primary)]";
  if (score < 4) return "text-red-500";
  if (score < 7) return "text-amber-500";
  return "text-emerald-500";
}

export function HealthKpiCards({ summary }: HealthKpiCardsProps) {
  return (
    <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
      <Card label="Files Analyzed">
        <p className="text-2xl font-bold text-[var(--color-text-primary)] tabular-nums">
          {formatNumber(summary.file_count)}
        </p>
      </Card>
      <Card label="Average Health">
        <p
          className={`text-2xl font-bold tabular-nums ${scoreColor(summary.average_health)}`}
        >
          {summary.average_health.toFixed(1)}
          <span className="text-base font-normal text-[var(--color-text-secondary)]">/10</span>
        </p>
      </Card>
      <Card label="Worst Performer">
        <p
          className={`text-2xl font-bold tabular-nums ${scoreColor(summary.worst_performer_score)}`}
        >
          {summary.worst_performer_score?.toFixed(1) ?? "—"}
          <span className="text-base font-normal text-[var(--color-text-secondary)]">/10</span>
        </p>
        <p className="text-xs text-[var(--color-text-tertiary)] mt-1 truncate">
          {summary.worst_performer_path ?? "no data"}
        </p>
      </Card>
      <Card label="Open Findings">
        <p className="text-2xl font-bold text-[var(--color-text-primary)] tabular-nums">
          {formatNumber(summary.open_findings)}
        </p>
      </Card>
    </div>
  );
}

function Card({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="rounded-lg border border-[var(--color-border-default)] bg-[var(--color-bg-surface)] p-4">
      <p className="text-xs font-medium text-[var(--color-text-tertiary)] uppercase tracking-wider mb-1">
        {label}
      </p>
      {children}
    </div>
  );
}
