import { formatNumber } from "../lib/format";
import { scoreTextColor, formatDelta, deltaColor } from "./tokens";
import { Sparkline } from "./sparkline";
import { SeverityDistribution, type SeverityBreakdown } from "./severity-distribution";

export interface HealthSummary {
  file_count: number;
  average_health: number;
  hotspot_health?: number | null;
  worst_performer_path: string | null;
  worst_performer_score: number | null;
  open_findings: number;
  severity_breakdown?: SeverityBreakdown;
}

export interface HealthKpiCardsProps {
  summary: HealthSummary;
  /** Optional series for sparklines, newest-last. */
  averageHistory?: number[];
  hotspotHistory?: number[];
  worstHistory?: number[];
  averageDelta?: number | null;
  hotspotDelta?: number | null;
}

export function HealthKpiCards({
  summary,
  averageHistory,
  hotspotHistory,
  worstHistory,
  averageDelta,
  hotspotDelta,
}: HealthKpiCardsProps) {
  return (
    <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-5">
      <Card label="Files Analyzed">
        <p className="text-2xl font-bold text-[var(--color-text-primary)] tabular-nums">
          {formatNumber(summary.file_count)}
        </p>
      </Card>
      <Card label="Average Health" sparkline={averageHistory} delta={averageDelta}>
        <p
          className={`text-2xl font-bold tabular-nums ${scoreTextColor(summary.average_health)}`}
        >
          {summary.average_health.toFixed(1)}
          <span className="text-base font-normal text-[var(--color-text-secondary)]">/10</span>
        </p>
      </Card>
      <Card
        label="Hotspot Health"
        sparkline={hotspotHistory}
        delta={hotspotDelta}
        hint="NLOC-weighted score over the repo's high-churn files."
      >
        <p
          className={`text-2xl font-bold tabular-nums ${scoreTextColor(summary.hotspot_health ?? null)}`}
        >
          {summary.hotspot_health == null ? "—" : summary.hotspot_health.toFixed(1)}
          {summary.hotspot_health == null ? null : (
            <span className="text-base font-normal text-[var(--color-text-secondary)]">/10</span>
          )}
        </p>
      </Card>
      <Card label="Worst Performer" sparkline={worstHistory}>
        <p
          className={`text-2xl font-bold tabular-nums ${scoreTextColor(summary.worst_performer_score)}`}
        >
          {summary.worst_performer_score?.toFixed(1) ?? "—"}
          <span className="text-base font-normal text-[var(--color-text-secondary)]">/10</span>
        </p>
        <p className="text-xs text-[var(--color-text-tertiary)] mt-1 truncate font-mono">
          {summary.worst_performer_path ?? "no data"}
        </p>
      </Card>
      <Card label="Open Findings">
        <p className="text-2xl font-bold text-[var(--color-text-primary)] tabular-nums">
          {formatNumber(summary.open_findings)}
        </p>
        {summary.severity_breakdown ? (
          <div className="mt-2">
            <SeverityDistribution
              breakdown={summary.severity_breakdown}
              showCounts={false}
            />
          </div>
        ) : null}
      </Card>
    </div>
  );
}

function Card({
  label,
  children,
  sparkline,
  delta,
  hint,
}: {
  label: string;
  children: React.ReactNode;
  sparkline?: number[] | undefined;
  delta?: number | null | undefined;
  hint?: string | undefined;
}) {
  return (
    <div
      className="rounded-lg border border-[var(--color-border-default)] bg-[var(--color-bg-surface)] p-4"
      title={hint}
    >
      <div className="flex items-start justify-between gap-2 mb-1">
        <p className="text-xs font-medium text-[var(--color-text-tertiary)] uppercase tracking-wider">
          {label}
        </p>
        {sparkline && sparkline.length > 0 ? (
          <div className="text-[var(--color-text-tertiary)]">
            <Sparkline values={sparkline} width={56} height={16} />
          </div>
        ) : null}
      </div>
      {children}
      {delta != null ? (
        <p className={`mt-1 text-[11px] tabular-nums ${deltaColor(delta)}`}>
          {formatDelta(delta)} vs. prior
        </p>
      ) : null}
    </div>
  );
}
