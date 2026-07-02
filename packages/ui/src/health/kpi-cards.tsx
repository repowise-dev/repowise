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
import { type SeverityBreakdown } from "./severity-distribution";
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

      {/* ── Operational stats: one quiet inline strip, not a second card grid ── */}
      <div className="flex flex-wrap items-center gap-x-6 gap-y-2 rounded-lg border border-[var(--color-border-default)] bg-[var(--color-bg-surface)] px-4 py-2.5">
        <InlineStat label="Files" value={formatNumber(summary.file_count)} />
        <InlineStat
          label="Hotspot health"
          value={summary.hotspot_health == null ? "—" : summary.hotspot_health.toFixed(1)}
          suffix={summary.hotspot_health == null ? undefined : "/10"}
          valueClass={scoreTextColor(summary.hotspot_health ?? null)}
          delta={hotspotDelta}
          sparkline={hotspotHistory}
          hint="The health score averaged over the repo's churn hotspots only (NLOC-weighted) — how healthy is the code you touch most?"
        />
        <InlineStat
          label="Worst performer"
          value={summary.worst_performer_score?.toFixed(1) ?? "—"}
          suffix={summary.worst_performer_score == null ? undefined : "/10"}
          valueClass={scoreTextColor(summary.worst_performer_score)}
          sub={
            summary.worst_performer_path
              ? summary.worst_performer_path.split("/").pop() ?? summary.worst_performer_path
              : undefined
          }
          subTitle={summary.worst_performer_path ?? undefined}
        />
        <InlineStat label="Open findings" value={formatNumber(summary.open_findings)} />
      </div>
    </div>
  );
}

/** A compact horizontal stat — tiny label, value, optional /10 suffix, sub-line,
 *  delta, and sparkline. Several sit in one slim strip below the signal tiles. */
function InlineStat({
  label,
  value,
  suffix,
  valueClass,
  sub,
  subTitle,
  delta,
  sparkline,
  hint,
}: {
  label: string;
  value: string;
  suffix?: string | undefined;
  valueClass?: string | undefined;
  sub?: string | undefined;
  subTitle?: string | undefined;
  delta?: number | null | undefined;
  sparkline?: number[] | undefined;
  hint?: string | undefined;
}) {
  return (
    <div className="flex flex-col">
      <span className="inline-flex items-center gap-1 text-[11px] font-medium uppercase tracking-wider text-[var(--color-text-tertiary)]">
        {label}
        {hint ? <InfoTip content={hint} label={`About ${label}`} /> : null}
      </span>
      <span className="flex items-baseline gap-1.5">
        <span className={`text-base font-semibold tabular-nums ${valueClass ?? "text-[var(--color-text-primary)]"}`}>
          {value}
          {suffix ? (
            <span className="text-xs font-normal text-[var(--color-text-secondary)]">{suffix}</span>
          ) : null}
        </span>
        {delta != null && Math.abs(delta) >= 0.005 ? (
          <span className={`text-[11px] tabular-nums ${deltaColor(delta)}`}>{formatDelta(delta)}</span>
        ) : null}
        {sparkline && sparkline.length > 1 ? (
          <span className="text-[var(--color-text-tertiary)]">
            <Sparkline values={sparkline} width={44} height={12} />
          </span>
        ) : null}
        {sub ? (
          <span
            className="max-w-[16rem] truncate font-mono text-xs text-[var(--color-text-tertiary)]"
            title={subTitle}
          >
            {sub}
          </span>
        ) : null}
      </span>
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
          {/* z-10 keeps this info button clickable above the tile's stretched
              click target (which is a sibling, never a wrapping button). */}
          <InfoTip content={hint} label={`About ${label}`} className="relative z-10" />
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
  return (
    <TileShell onClick={onClick} ariaLabel={`View ${label} findings`}>
      {inner}
    </TileShell>
  );
}

/** Shared chrome for a hero signal tile: a static card, or a card with a
 *  stretched click target when an `onClick` makes it a jump into the
 *  pillar-filtered view. The click target is a sibling button covering the
 *  card rather than a button wrapping the content, so the header's info button
 *  is not an (invalid) interactive descendant of another button. */
function TileShell({
  onClick,
  ariaLabel,
  children,
}: {
  onClick?: (() => void) | undefined;
  ariaLabel?: string | undefined;
  children: React.ReactNode;
}) {
  const cls =
    "relative flex w-full flex-col rounded-xl border border-[var(--color-border-default)] bg-[var(--color-bg-surface)] p-4 text-left transition-colors";
  if (onClick) {
    return (
      <div className={`${cls} hover:border-[var(--color-border-strong)]`}>
        {children}
        <button
          type="button"
          aria-label={ariaLabel}
          onClick={onClick}
          className="absolute inset-0 cursor-pointer rounded-xl focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent-primary)]"
        />
      </div>
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
        <InfoTip content={PERF_HINT} label="About Performance" className="relative z-10" />
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
  return (
    <TileShell onClick={interactive ? onClick : undefined} ariaLabel="View Performance findings">
      {inner}
    </TileShell>
  );
}
