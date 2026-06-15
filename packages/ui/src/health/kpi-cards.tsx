import type { HealthBand, HealthDistribution } from "@repowise-dev/types/health";
import { bandForScore, HEALTH_BAND_LABEL } from "@repowise-dev/types";
import { formatNumber } from "../lib/format";
import { InfoTip } from "../shared/info-tip";
import { scoreTextColor, healthBandTextColor, formatDelta, deltaColor } from "./tokens";
import { Sparkline } from "./sparkline";
import { SeverityDistribution, type SeverityBreakdown } from "./severity-distribution";
import { HealthDistributionBar } from "./health-distribution-bar";

export interface HealthSummary {
  file_count: number;
  average_health: number;
  hotspot_health?: number | null;
  worst_performer_path: string | null;
  worst_performer_score: number | null;
  open_findings: number;
  severity_breakdown?: SeverityBreakdown;
  /** Repo-level band from the API; derived from average_health when absent. */
  band?: HealthBand;
}

export interface HealthKpiCardsProps {
  summary: HealthSummary;
  /** NLOC-weighted file distribution across the 3 bands. */
  distribution?: HealthDistribution | null;
  /** Optional series for sparklines, newest-last. */
  averageHistory?: number[];
  hotspotHistory?: number[];
  worstHistory?: number[];
  averageDelta?: number | null;
  hotspotDelta?: number | null;
}

export function HealthKpiCards({
  summary,
  distribution,
  averageHistory,
  hotspotHistory,
  worstHistory,
  averageDelta,
  hotspotDelta,
}: HealthKpiCardsProps) {
  const band = summary.band ?? bandForScore(summary.average_health);
  return (
    <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-5">
      <Card label="Files Analyzed">
        <p className="text-2xl font-bold text-[var(--color-text-primary)] tabular-nums">
          {formatNumber(summary.file_count)}
        </p>
      </Card>
      <Card label="Average Health" sparkline={averageHistory} delta={averageDelta}>
        <p
          className={`flex items-baseline gap-2 text-2xl font-bold tabular-nums ${scoreTextColor(summary.average_health)}`}
        >
          <span>
            {summary.average_health.toFixed(1)}
            <span className="text-base font-normal text-[var(--color-text-secondary)]">/10</span>
          </span>
          <span className={`text-xs font-semibold uppercase tracking-wide ${healthBandTextColor(band)}`}>
            {HEALTH_BAND_LABEL[band]}
          </span>
        </p>
        {distribution ? (
          <div className="mt-2">
            <HealthDistributionBar distribution={distribution} showCounts={false} height="sm" />
          </div>
        ) : null}
      </Card>
      <Card
        label="Hotspot Health"
        sparkline={hotspotHistory}
        delta={hotspotDelta}
        hint="The health score averaged over the repo's churn hotspots only (NLOC-weighted). Not a hotspot count — it answers “how healthy is the code you touch most?”"
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
    <div className="rounded-lg border border-[var(--color-border-default)] bg-[var(--color-bg-surface)] p-4">
      <div className="flex items-start justify-between gap-2 mb-1">
        <p className="inline-flex items-center gap-1 text-xs font-medium text-[var(--color-text-tertiary)] uppercase tracking-wider">
          {label}
          {hint ? <InfoTip content={hint} label={`About ${label}`} /> : null}
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
