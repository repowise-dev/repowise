import {
  HeartPulse,
  Flame,
  TrendingUp,
  TrendingDown,
  ShieldAlert,
  ArrowRight,
  Wrench,
  Gauge,
  Target,
} from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "../ui/card";
import { fileEntityPath } from "../shared/entity/routes";
import { truncatePath } from "../lib/format";
import { Sparkline } from "../health/sparkline";

export interface HealthOverviewPoint {
  taken_at: string | null;
  average_health: number;
  hotspot_health: number;
}

export interface HealthOverviewData {
  /** Repo-wide biomarker health average, 1–10 (null until first health run). */
  average_health: number | null;
  /** Health of the highest-churn files, 1–10. */
  hotspot_health: number | null;
  /** Lowest-scoring file and its score (1–10). */
  worst_performer_path: string | null;
  worst_performer_score: number | null;
  /** Open biomarker findings, with a severity rollup. */
  open_findings: number;
  severity_breakdown: Record<string, number>;
  /** Co-equal maintainability pillar headline (1–10), null until measured. */
  maintainability_average?: number | null;
  /** Co-equal performance pillar headline (1–10, static risk), null until measured. */
  performance_average?: number | null;
  /** Open findings homing under the performance pillar — the actionable count. */
  performance_findings?: number;
  /** Snapshot history, oldest→newest, for the trend sparkline. */
  history: HealthOverviewPoint[];
  snapshot_count: number;
}

/**
 * Structural slice of the defect-accuracy rollup this card needs. Matches the
 * slim `StatsDefectAccuracy` from stats-highlights as well as the full
 * `DefectAccuracy` from health/overview, so either payload fits.
 */
export interface HealthOverviewAccuracy {
  /** How many of the K lowest-health files were bug-fixed in the window. */
  hits: number;
  k: number;
  /** Precision over the repo-wide base rate; null when not computable. */
  lift: number | null;
  base_rate: number;
  window_days: number;
}

interface HealthOverviewCardProps {
  data: HealthOverviewData;
  repoId: string;
  /** Delta vs the previous snapshot (1–10 scale); drives the trend chips. */
  averageDelta?: number | null;
  hotspotDelta?: number | null;
  /**
   * "Does this score find the bugs?" validation, rendered as a thin strip under
   * the scores. Null/omitted when the repo lacks enough files or defect history
   * to answer honestly — in which case the strip hides rather than guessing.
   */
  defectAccuracy?: HealthOverviewAccuracy | null;
  className?: string;
}

/* 1–10 health bands — the moat metric, distinct from the 0–100 composite
   shown in the header badge. */
function band(v: number): { color: string; label: string } {
  if (v >= 8) return { color: "var(--color-success)", label: "Excellent" };
  if (v >= 6.5) return { color: "var(--color-success)", label: "Good" };
  if (v >= 5) return { color: "var(--color-caution)", label: "Fair" };
  if (v >= 3.5) return { color: "var(--color-warning)", label: "Needs work" };
  return { color: "var(--color-error)", label: "Critical" };
}

const SEVERITY_ORDER = ["critical", "high", "medium", "low"] as const;
const SEVERITY_COLOR: Record<string, string> = {
  critical: "var(--color-error)",
  high: "var(--color-accent-fill)",
  medium: "var(--color-warning)",
  low: "var(--color-text-tertiary)",
};

function TrendChip({ delta }: { delta: number | null | undefined }) {
  if (delta == null || Math.abs(delta) < 0.05) return null;
  const up = delta > 0;
  const Icon = up ? TrendingUp : TrendingDown;
  return (
    <span
      className="inline-flex items-center gap-0.5 text-xs font-medium tabular-nums"
      style={{ color: up ? "var(--color-success)" : "var(--color-error)" }}
      title={`${up ? "+" : ""}${delta.toFixed(2)} vs previous snapshot`}
    >
      <Icon className="h-3 w-3" />
      {up ? "+" : ""}
      {delta.toFixed(1)}
    </span>
  );
}

/**
 * Open findings as one quiet line: label, a thin severity-mix bar, and the
 * count. Findings are a secondary read next to the health scores above, so this
 * deliberately stays a single row — no donut, no headline number, no legend
 * block. The severity split lives in the segment tooltips and, in full, one
 * click away on the triage tab.
 */
function FindingsLine({
  href,
  segments,
  total,
  count,
}: {
  href: string;
  segments: { key: string; count: number; color: string }[];
  total: number;
  count: number;
}) {
  return (
    <a
      href={href}
      className="group flex items-center gap-3 text-xs text-[var(--color-text-tertiary)] transition-colors hover:text-[var(--color-accent-primary)]"
    >
      <span className="flex shrink-0 items-center gap-1.5 font-medium uppercase tracking-wider">
        <ShieldAlert className="h-3 w-3" />
        Open findings
      </span>
      <span
        className="flex h-1.5 min-w-0 flex-1 overflow-hidden rounded-full bg-[var(--color-bg-inset)]"
        title={segments.map((s) => `${s.key}: ${s.count.toLocaleString()}`).join(" · ")}
      >
        {segments.map((s) => (
          <span
            key={s.key}
            className="h-full"
            style={{ width: `${(s.count / total) * 100}%`, background: s.color }}
          />
        ))}
      </span>
      <span className="shrink-0 tabular-nums text-[var(--color-text-secondary)]">
        {count.toLocaleString()}
      </span>
    </a>
  );
}

function MetricTile({
  icon,
  label,
  value,
  delta,
  band: b,
  sparkline,
}: {
  icon: React.ReactNode;
  label: string;
  value: number | null;
  delta?: number | null | undefined;
  band: { color: string; label: string } | null;
  sparkline?: number[] | undefined;
}) {
  return (
    <div className="flex flex-col gap-1.5 px-4 py-3.5">
      <div className="flex items-center gap-1.5 text-xs font-medium uppercase tracking-wider text-[var(--color-text-tertiary)]">
        {icon}
        {label}
      </div>
      {value == null ? (
        <p className="text-sm text-[var(--color-text-tertiary)]">No data yet</p>
      ) : (
        <>
          <div className="flex items-baseline gap-1.5">
            <span className="text-3xl font-bold tabular-nums leading-none" style={{ color: b?.color }}>
              {value.toFixed(1)}
            </span>
            <span className="text-sm text-[var(--color-text-tertiary)]">/10</span>
            <TrendChip delta={delta} />
          </div>
          <div className="flex items-center justify-between gap-2">
            {b && (
              <span
                className="text-xs font-medium uppercase tracking-wide"
                style={{ color: b.color }}
              >
                {b.label}
              </span>
            )}
            {sparkline && sparkline.length >= 2 && (
              <Sparkline
                values={sparkline}
                width={96}
                height={28}
                stroke="var(--color-accent-primary)"
                fill="color-mix(in srgb, var(--color-accent-fill) 20%, transparent)"
              />
            )}
          </div>
        </>
      )}
    </div>
  );
}

/**
 * Thin self-validation strip: of the K lowest-health files, how many actually
 * got bug-fixed in the recent window, against the repo's own base rate. The
 * scores above are a claim; this is the one line that says whether the claim
 * holds on *this* repo. Full breakdown (per-K table, the flagged files, the
 * honesty note) lives on the Code Health page.
 */
function AccuracyStrip({ data, href }: { data: HealthOverviewAccuracy; href: string }) {
  const months = Math.max(1, Math.round(data.window_days / 30));
  const windowLabel = months === 1 ? "month" : `${months} months`;
  const basePct = Math.round(data.base_rate * 100);

  return (
    <a
      href={href}
      className="group flex flex-wrap items-center gap-x-2 gap-y-0.5 border-t border-[var(--color-border-default)] px-4 py-2.5 text-xs transition-colors hover:bg-[var(--color-bg-elevated)]"
    >
      <Target className="h-3 w-3 shrink-0 text-[var(--color-accent-primary)]" aria-hidden />
      <span className="font-medium text-[var(--color-text-secondary)]">
        <span className="tabular-nums text-[var(--color-text-primary)]">
          {data.hits}/{data.k}
        </span>{" "}
        lowest-health files were bug-fixed in the last {windowLabel}
      </span>
      {data.lift != null && (
        <span className="font-semibold tabular-nums text-[var(--color-accent-primary)]">
          {data.lift}× the {basePct}% baseline
        </span>
      )}
    </a>
  );
}

/**
 * Code Health hero for the Overview page — the product's moat surfaced first.
 * Renders the repo-wide biomarker health and hotspot health (1–10, with trend
 * and delta), the open-findings severity mix, and the single worst-scoring
 * file, all from the overview-summary payload (no extra fetch). A "View
 * report" link jumps to the full Code Health page.
 */
export function HealthOverviewCard({
  data,
  repoId,
  averageDelta,
  hotspotDelta,
  defectAccuracy,
  className,
}: HealthOverviewCardProps) {
  const reportHref = `/repos/${repoId}/code-health`;
  const avg = data.average_health;
  const hot = data.hotspot_health;
  const avgSeries = data.history.map((p) => p.average_health);
  const hotSeries = data.history.map((p) => p.hotspot_health);

  const severities = SEVERITY_ORDER.map((key) => ({
    key,
    count: data.severity_breakdown[key] ?? 0,
    color: SEVERITY_COLOR[key]!,
  })).filter((s) => s.count > 0);
  const severityTotal = severities.reduce((s, x) => s + x.count, 0);

  const hasData = avg != null || hot != null;

  return (
    <Card className={`overflow-hidden ${className ?? ""}`}>
      <CardHeader className="pb-2">
        <CardTitle className="text-sm flex items-center justify-between">
          <span className="flex items-center gap-2">
            <HeartPulse className="h-4 w-4 text-[var(--color-success)]" />
            Code Health
          </span>
          <a
            href={reportHref}
            className="inline-flex items-center gap-1 text-xs font-normal text-[var(--color-accent-primary)] hover:underline"
          >
            View report <ArrowRight className="h-3 w-3" />
          </a>
        </CardTitle>
      </CardHeader>
      <CardContent className="p-0">
        {!hasData ? (
          <p className="px-4 pb-4 text-sm text-[var(--color-text-secondary)]">
            Health scores land with the first index: complexity, duplication,
            coverage, churn, and ownership across the repo.
          </p>
        ) : (
          <>
            {/* Two headline metrics */}
            <div className="grid grid-cols-1 sm:grid-cols-2 divide-y sm:divide-y-0 sm:divide-x divide-[var(--color-border-default)] border-b border-[var(--color-border-default)]">
              <MetricTile
                icon={<HeartPulse className="h-3 w-3" />}
                label="Average health"
                value={avg}
                delta={averageDelta}
                band={avg != null ? band(avg) : null}
                sparkline={avgSeries}
              />
              <MetricTile
                icon={<Flame className="h-3 w-3" />}
                label="Hotspot health"
                value={hot}
                delta={hotspotDelta}
                band={hot != null ? band(hot) : null}
                sparkline={hotSeries}
              />
            </div>

            {/* The scores above, checked against this repo's own git history. */}
            {defectAccuracy && (
              <AccuracyStrip data={defectAccuracy} href={`${reportHref}?tab=triage`} />
            )}

            {/* Findings + worst performer */}
            <div className="px-4 py-3.5 space-y-3">
              {severityTotal > 0 ? (
                <FindingsLine
                  href={`${reportHref}?tab=triage`}
                  segments={severities}
                  total={severityTotal}
                  count={data.open_findings}
                />
              ) : (
                <a
                  href={`${reportHref}?tab=triage`}
                  className="flex items-center gap-1.5 text-xs text-[var(--color-success)] hover:underline"
                >
                  <ShieldAlert className="h-3 w-3" />
                  No open findings — clean bill of health.
                </a>
              )}

              {data.worst_performer_path && data.worst_performer_score != null && (
                <a
                  href={fileEntityPath(`/repos/${repoId}`, data.worst_performer_path)}
                  className="flex items-center justify-between gap-3 -mx-2 mt-1 rounded-md px-2 py-1.5 transition-colors hover:bg-[var(--color-bg-elevated)] group"
                >
                  <span className="flex min-w-0 items-center gap-1.5">
                    <Flame className="h-3 w-3 shrink-0 text-[var(--color-error)]" />
                    <span className="text-[10px] uppercase tracking-wider text-[var(--color-text-tertiary)] shrink-0">
                      Worst file
                    </span>
                    <span className="truncate font-mono text-xs text-[var(--color-text-secondary)] group-hover:text-[var(--color-accent-primary)] transition-colors">
                      {truncatePath(data.worst_performer_path, 40)}
                    </span>
                  </span>
                  <span
                    className="shrink-0 text-xs font-bold tabular-nums"
                    style={{ color: band(data.worst_performer_score).color }}
                  >
                    {data.worst_performer_score.toFixed(1)}/10
                  </span>
                </a>
              )}
            </div>

            {/* The two co-equal pillars beside the defect headline above —
                maintainability and performance, each a jump into its findings. */}
            {(data.maintainability_average != null || data.performance_average != null) && (
              <div className="grid grid-cols-2 divide-x divide-[var(--color-border-default)] border-t border-[var(--color-border-default)]">
                <PillarStat
                  href={`${reportHref}?pillar=maintainability`}
                  icon={<Wrench className="h-3 w-3" />}
                  label="Maintainability"
                  score={data.maintainability_average ?? null}
                />
                <PerformancePillarStat
                  href={`${reportHref}?pillar=performance`}
                  score={data.performance_average ?? null}
                  findings={data.performance_findings ?? 0}
                />
              </div>
            )}
          </>
        )}
      </CardContent>
    </Card>
  );
}

/** A compact pillar cell in the Overview health card — label + score/10 + band,
 *  the whole cell a link into that pillar's filtered findings. */
function PillarStat({
  href,
  icon,
  label,
  score,
}: {
  href: string;
  icon: React.ReactNode;
  label: string;
  score: number | null;
}) {
  const b = score != null ? band(score) : null;
  return (
    <a
      href={href}
      className="flex flex-col gap-1 px-4 py-3 transition-colors hover:bg-[var(--color-bg-elevated)]"
    >
      <span className="flex items-center gap-1.5 text-[10px] font-medium uppercase tracking-wider text-[var(--color-text-tertiary)]">
        {icon}
        {label}
      </span>
      {score == null ? (
        <span className="text-sm text-[var(--color-text-tertiary)]">Not measured</span>
      ) : (
        <span className="flex items-baseline gap-1.5">
          <span className="text-xl font-bold tabular-nums leading-none" style={{ color: b?.color }}>
            {score.toFixed(1)}
          </span>
          <span className="text-xs text-[var(--color-text-tertiary)]">/10</span>
          {b ? (
            <span className="text-[10px] font-medium uppercase tracking-wide" style={{ color: b.color }}>
              {b.label}
            </span>
          ) : null}
        </span>
      )}
    </a>
  );
}

/** Performance is a risk pillar, so its cell leads with the count of open risks
 *  (the actionable read) and keeps the /10 score as a quiet secondary line. */
function PerformancePillarStat({
  href,
  score,
  findings,
}: {
  href: string;
  score: number | null;
  findings: number;
}) {
  const clear = score != null && findings === 0;
  return (
    <a
      href={href}
      className="flex flex-col gap-1 px-4 py-3 transition-colors hover:bg-[var(--color-bg-elevated)]"
    >
      <span className="flex items-center gap-1.5 text-[10px] font-medium uppercase tracking-wider text-[var(--color-text-tertiary)]">
        <Gauge className="h-3 w-3" />
        Performance
      </span>
      {score == null ? (
        <span className="text-sm text-[var(--color-text-tertiary)]">Not measured</span>
      ) : clear ? (
        <span className="flex items-baseline gap-1.5">
          <span className="text-xl font-bold tabular-nums leading-none text-[var(--color-success)]">
            0
          </span>
          <span className="text-[10px] font-medium uppercase tracking-wide text-[var(--color-success)]">
            All clear
          </span>
        </span>
      ) : (
        <span className="flex items-baseline gap-1.5">
          <span className="text-xl font-bold tabular-nums leading-none text-[var(--color-text-primary)]">
            {findings.toLocaleString()}
          </span>
          <span className="text-xs text-[var(--color-text-secondary)]">
            {findings === 1 ? "risk" : "risks"}
          </span>
          <span className="text-[10px] tabular-nums text-[var(--color-text-tertiary)]">
            · {score.toFixed(1)}/10
          </span>
        </span>
      )}
    </a>
  );
}
