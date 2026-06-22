import type { HealthBand, HealthDistribution } from "@repowise-dev/types/health";
import { bandForScore, HEALTH_BAND_LABEL } from "@repowise-dev/types";
import { HeartPulse, Wrench, Gauge } from "lucide-react";
import { formatNumber } from "../lib/format";
import { InfoTip } from "../shared/info-tip";
import {
  scoreTextColor,
  healthBandTextColor,
  healthBandSoftBadgeClass,
  formatDelta,
  deltaColor,
} from "./tokens";
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
  /** Maintainability pillar headline (the co-surfaced second signal). `null`
   *  when no file carries a maintainability score yet. */
  maintainability_average?: number | null;
  /** Performance pillar headline (the co-surfaced third signal: static
   *  performance RISK). `null` when no file carries a performance score yet. */
  performance_average?: number | null;
  /** Open findings homing under each pillar — the actionable counts. */
  maintainability_findings?: number;
  performance_findings?: number;
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
  /** Jump to the pillar-filtered findings view. When provided, the
   *  Maintainability + Performance signal tiles become interactive. */
  onSelectPillar?: (pillar: "defect" | "maintainability" | "performance") => void;
}

const DEFECT_HINT =
  "The overall health headline: the defect-risk signal, calibrated against real bugs from complexity, duplication, coverage, churn, and ownership. NLOC-weighted across the repo.";
const MAINT_HINT =
  "A co-equal second signal: the smells that hurt readability and change-cost (low cohesion, brain methods, primitive obsession, duplication, error handling), scored on their own and never blended into the defect score.";
const PERF_HINT =
  "A co-equal third signal: static performance RISK — I/O-in-loop / N+1 shapes (a DB, network, filesystem, or subprocess call per loop iteration), detected across function boundaries via the call graph. High precision, low recall; never blended into the defect score.";

export function HealthKpiCards({
  summary,
  distribution,
  averageHistory,
  hotspotHistory,
  worstHistory,
  averageDelta,
  hotspotDelta,
  onSelectPillar,
}: HealthKpiCardsProps) {
  const band = summary.band ?? bandForScore(summary.average_health);
  const maint = summary.maintainability_average;
  const perf = summary.performance_average;
  const perfFindings = summary.performance_findings ?? 0;
  const maintFindings = summary.maintainability_findings ?? 0;

  return (
    <div className="space-y-3">
      {/* ── The three signals: the product's health model, given top billing ── */}
      <div className="grid grid-cols-1 gap-4 md:grid-cols-3">
        <SignalTile
          icon={<HeartPulse className="h-4 w-4" />}
          label="Defect risk"
          hint={DEFECT_HINT}
          score={summary.average_health}
          band={band}
          delta={averageDelta}
          sparkline={averageHistory}
        >
          {distribution ? (
            <div className="mt-3">
              <HealthDistributionBar distribution={distribution} showCounts={false} height="sm" />
            </div>
          ) : null}
        </SignalTile>

        <SignalTile
          icon={<Wrench className="h-4 w-4" />}
          label="Maintainability"
          hint={MAINT_HINT}
          score={maint ?? null}
          band={maint != null ? bandForScore(maint) : null}
          footnote={
            maint != null && maintFindings > 0
              ? `${formatNumber(maintFindings)} ${maintFindings === 1 ? "finding" : "findings"}`
              : maint != null
                ? "No open findings"
                : undefined
          }
          onClick={maint != null && onSelectPillar ? () => onSelectPillar("maintainability") : undefined}
        />

        <PerformanceTile
          score={perf ?? null}
          findings={perfFindings}
          onClick={perf != null && onSelectPillar ? () => onSelectPillar("performance") : undefined}
        />
      </div>

      {/* ── Operational stats: the supporting strip, visually quieter ── */}
      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <StatCard label="Files Analyzed">
          <p className="text-xl font-semibold text-[var(--color-text-primary)] tabular-nums">
            {formatNumber(summary.file_count)}
          </p>
        </StatCard>
        <StatCard
          label="Hotspot Health"
          sparkline={hotspotHistory}
          delta={hotspotDelta}
          hint="The health score averaged over the repo's churn hotspots only (NLOC-weighted) — how healthy is the code you touch most?"
        >
          <p className={`text-xl font-semibold tabular-nums ${scoreTextColor(summary.hotspot_health ?? null)}`}>
            {summary.hotspot_health == null ? "—" : summary.hotspot_health.toFixed(1)}
            {summary.hotspot_health == null ? null : (
              <span className="text-sm font-normal text-[var(--color-text-secondary)]">/10</span>
            )}
          </p>
        </StatCard>
        <StatCard label="Worst Performer" sparkline={worstHistory}>
          <p className={`text-xl font-semibold tabular-nums ${scoreTextColor(summary.worst_performer_score)}`}>
            {summary.worst_performer_score?.toFixed(1) ?? "—"}
            <span className="text-sm font-normal text-[var(--color-text-secondary)]">/10</span>
          </p>
          <p className="text-xs text-[var(--color-text-tertiary)] mt-1 truncate font-mono">
            {summary.worst_performer_path ?? "no data"}
          </p>
        </StatCard>
        <StatCard label="Open Findings">
          <p className="text-xl font-semibold text-[var(--color-text-primary)] tabular-nums">
            {formatNumber(summary.open_findings)}
          </p>
          {summary.severity_breakdown ? (
            <div className="mt-2">
              <SeverityDistribution breakdown={summary.severity_breakdown} showCounts={false} />
            </div>
          ) : null}
        </StatCard>
      </div>
    </div>
  );
}

/** A hero signal tile: icon + label, a large score/10 with band badge, and an
 *  optional supporting line. Becomes a button when `onClick` is provided. */
function SignalTile({
  icon,
  label,
  hint,
  score,
  band,
  delta,
  sparkline,
  footnote,
  onClick,
  children,
}: {
  icon: React.ReactNode;
  label: string;
  hint: string;
  score: number | null;
  band: HealthBand | null;
  delta?: number | null | undefined;
  sparkline?: number[] | undefined;
  footnote?: string | undefined;
  onClick?: (() => void) | undefined;
  children?: React.ReactNode;
}) {
  const inner = (
    <>
      <div className="flex items-center justify-between gap-2">
        <span className="inline-flex items-center gap-1.5 text-xs font-medium uppercase tracking-wider text-[var(--color-text-tertiary)]">
          <span className="text-[var(--color-text-secondary)]">{icon}</span>
          {label}
          <InfoTip content={hint} label={`About ${label}`} />
        </span>
        {sparkline && sparkline.length > 1 ? (
          <span className="text-[var(--color-text-tertiary)]">
            <Sparkline values={sparkline} width={56} height={16} />
          </span>
        ) : null}
      </div>

      {score == null ? (
        <p className="mt-3 text-3xl font-bold tabular-nums text-[var(--color-text-tertiary)]">
          —
          <span className="ml-2 align-middle text-xs font-normal">not measured</span>
        </p>
      ) : (
        <div className="mt-3 flex items-baseline gap-2.5">
          <span className={`text-3xl font-bold leading-none tabular-nums ${scoreTextColor(score)}`}>
            {score.toFixed(1)}
            <span className="text-base font-normal text-[var(--color-text-secondary)]">/10</span>
          </span>
          {band ? (
            <span
              className={`rounded-full px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide ${healthBandSoftBadgeClass(band)}`}
            >
              {HEALTH_BAND_LABEL[band]}
            </span>
          ) : null}
        </div>
      )}

      {delta != null && Math.abs(delta) >= 0.005 ? (
        <p className={`mt-1.5 text-xs tabular-nums ${deltaColor(delta)}`}>
          {formatDelta(delta)} vs. prior
        </p>
      ) : footnote ? (
        <p className="mt-1.5 text-xs text-[var(--color-text-tertiary)]">{footnote}</p>
      ) : null}

      {children}
    </>
  );
  return <TileShell onClick={onClick}>{inner}</TileShell>;
}

/** Shared chrome for a hero signal tile — a static card, or a button when an
 *  `onClick` makes it a jump into the pillar-filtered view. */
function TileShell({
  onClick,
  children,
}: {
  onClick?: (() => void) | undefined;
  children: React.ReactNode;
}) {
  const cls =
    "flex w-full flex-col rounded-xl border border-[var(--color-border-default)] bg-[var(--color-bg-surface)] p-5 text-left transition-colors";
  if (onClick) {
    return (
      <button
        type="button"
        onClick={onClick}
        className={`${cls} cursor-pointer hover:border-[var(--color-border-strong)]`}
      >
        {children}
      </button>
    );
  }
  return <div className={cls}>{children}</div>;
}

/** Performance is a RISK pillar — so it leads with the count of open risks, with
 *  the /10 score as a calm secondary read. Clean repos show "0 · all clear". */
function PerformanceTile({
  score,
  findings,
  onClick,
}: {
  score: number | null;
  findings: number;
  onClick?: (() => void) | undefined;
}) {
  const interactive = !!onClick && (findings > 0 || score != null);
  const band = score != null ? bandForScore(score) : null;
  const clear = score != null && findings === 0;

  const inner = (
    <>
      <span className="inline-flex items-center gap-1.5 text-xs font-medium uppercase tracking-wider text-[var(--color-text-tertiary)]">
        <span className="text-[var(--color-text-secondary)]">
          <Gauge className="h-4 w-4" />
        </span>
        Performance
        <InfoTip content={PERF_HINT} label="About Performance" />
      </span>

      {score == null ? (
        <p className="mt-3 text-3xl font-bold tabular-nums text-[var(--color-text-tertiary)]">
          —
          <span className="ml-2 align-middle text-xs font-normal">not measured</span>
        </p>
      ) : clear ? (
        <>
          <div className="mt-3 flex items-baseline gap-2.5">
            <span className="text-3xl font-bold leading-none tabular-nums text-[var(--color-success)]">
              0
            </span>
            <span className="rounded-full bg-[var(--color-success)]/15 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-[var(--color-success)]">
              All clear
            </span>
          </div>
          <p className="mt-1.5 text-xs text-[var(--color-text-tertiary)] tabular-nums">
            Risk score {score.toFixed(1)}/10
          </p>
        </>
      ) : (
        <>
          <div className="mt-3 flex items-baseline gap-1.5">
            <span className="text-3xl font-bold leading-none tabular-nums text-[var(--color-text-primary)]">
              {formatNumber(findings)}
            </span>
            <span className="text-sm font-medium text-[var(--color-text-secondary)]">
              {findings === 1 ? "risk" : "risks"}
            </span>
          </div>
          <p className="mt-1.5 inline-flex items-center gap-1.5 text-xs tabular-nums text-[var(--color-text-tertiary)]">
            Risk score {score.toFixed(1)}/10
            {band ? (
              <span className={`font-semibold uppercase tracking-wide ${healthBandTextColor(band)}`}>
                {HEALTH_BAND_LABEL[band]}
              </span>
            ) : null}
          </p>
        </>
      )}
    </>
  );
  return <TileShell onClick={interactive ? onClick : undefined}>{inner}</TileShell>;
}

function StatCard({
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
        <p className={`mt-1 text-xs tabular-nums ${deltaColor(delta)}`}>
          {formatDelta(delta)} vs. prior
        </p>
      ) : null}
    </div>
  );
}
