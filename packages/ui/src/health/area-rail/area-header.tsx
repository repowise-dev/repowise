"use client";

/**
 * The top of an area card: the score with its band word, how it moved, the
 * trend, and the band mix. One figure and its reading, then the shape.
 */

import { HEALTH_BAND_LABEL, HEALTH_BAND_ORDER, formatScore, type HealthBand } from "@repowise-dev/types/health";
import type { AreaDetail } from "@repowise-dev/types/areas";

import { ProportionBar } from "../../shared/proportion-bar";
import { Sparkline } from "../sparkline";
import { HEALTH_BAND_SEGMENT, deltaColor, formatDelta, healthBandTextColor } from "../tokens";
import type { AreaRailLabels } from "./labels";

/** Days the delta window actually spans, rounded, or the target when unknown. */
function windowDays(detail: AreaDetail): number {
  const { latest_at, baseline_at, target_days } = detail.delta_window;
  if (!latest_at || !baseline_at) return target_days;
  const ms = Date.parse(latest_at) - Date.parse(baseline_at);
  return Number.isFinite(ms) && ms > 0 ? Math.max(1, Math.round(ms / 86_400_000)) : target_days;
}

export function AreaHeader({
  detail,
  eyebrow,
  scope,
  title,
  labels,
}: {
  detail: AreaDetail;
  eyebrow: string;
  /** What the figures cover ("Production code"), when the map holds files out. */
  scope?: string | undefined;
  title: string;
  labels: AreaRailLabels;
}) {
  const a = detail.area;
  const band = a.band as HealthBand;
  const delta = a.health_delta_30d;
  const days = windowDays(detail);
  const trend = detail.trend.map((p) => p.health);
  return (
    <header className="flex flex-col gap-3">
      <div className="flex min-w-0 flex-col gap-0.5">
        <span className="font-mono text-[10px] uppercase tracking-[0.12em] text-[var(--color-text-tertiary)]">
          {[eyebrow, scope, labels.files(a.file_count)].filter(Boolean).join(" · ")}
        </span>
        <h3 className="break-all font-mono text-[15px] font-semibold leading-snug text-[var(--color-text-primary)]">
          {title}
        </h3>
      </div>
      <div className="flex items-end justify-between gap-3">
        <div className="flex min-w-0 flex-col">
          <span className="flex items-baseline gap-2">
            <span className={`font-mono text-[22px] font-semibold tabular-nums leading-none ${healthBandTextColor(band)}`}>
              {formatScore(a.health)}
            </span>
            <span className={`text-xs font-medium ${healthBandTextColor(band)}`}>{HEALTH_BAND_LABEL[band]}</span>
          </span>
          <span className={`mt-1 font-mono text-xs tabular-nums ${deltaColor(delta)}`}>
            {delta == null ? labels.noDelta : labels.deltaWindow(formatDelta(delta), days)}
          </span>
        </div>
        {trend.length > 1 ? (
          <span className="shrink-0 text-[var(--color-text-tertiary)]">
            <Sparkline values={trend} width={96} height={28} />
          </span>
        ) : null}
      </div>
      {delta != null && delta > 0 ? (
        <p data-testid="area-improved" className="text-xs text-[var(--color-success)]">
          {labels.improved(formatDelta(delta), days)}
        </p>
      ) : null}
      <ProportionBar
        size="sm"
        sort={false}
        legend={false}
        label="Lines by health band"
        segments={HEALTH_BAND_ORDER.map((b) => ({
          key: b,
          label: HEALTH_BAND_LABEL[b],
          value: detail.distribution.bands[b].nloc,
          detail: `${detail.distribution.bands[b].pct}%`,
          color: HEALTH_BAND_SEGMENT[b],
        }))}
      />
    </header>
  );
}
