"use client";

/**
 * Trend view — the KPI history over recent indexes, on the section design
 * language.
 *
 * It used to open with three `MetricCard`s in a grid, then stack tinted alert
 * boxes and two dashed empty-state boxes under them: six bordered containers at
 * near-identical weight for what is really two numbers, a warning and a chart.
 * The figures are a hairline `StatRibbon` now, the alerts are rows that spend
 * their colour on the icon and the label rather than on a ground, and the empty
 * states are sentences saying what will fill them.
 *
 * The single-snapshot case stays explicit. A trend line drawn through one point
 * is a flat line, which reads as "stable" when the truth is "not measured yet".
 *
 * Trend data is fetched once by the host and passed in, so it is not
 * double-fetched alongside anything else on the page.
 */

import { AlertTriangle } from "lucide-react";
import type { HealthTrendResponse } from "@repowise-dev/types/health";

import { Skeleton } from "../ui/skeleton";
import { StatRibbon, type RibbonStat } from "../stats/stat-ribbon";

import { TrendChart } from "./trend-chart";
import { TrendSlopeChart } from "./trend-slope-chart";
import { deltaColor, formatDelta, scoreTextColor } from "./tokens";

export function TrendView({
  data,
  isLoading,
  error,
}: {
  data: HealthTrendResponse | undefined;
  isLoading: boolean;
  error: unknown;
}) {
  if (isLoading) return <Skeleton className="h-64 w-full rounded-lg" />;
  if (error || !data) {
    return (
      <p className="text-sm text-[var(--color-text-secondary)]">
        Couldn&apos;t load trend data. Try refreshing.
      </p>
    );
  }

  const singleSnapshot = data.snapshot_count <= 1;
  const { summary } = data;

  // "0.00 vs. 7.5" is the shape a delta takes when nothing happened, and it
  // reads as a measurement rather than as "no news". Say it plainly instead.
  const deltaSub = (delta: number | null, previous: number | null) => {
    if (delta == null) return "no prior snapshot";
    if (Math.abs(delta) < 0.05) return "unchanged since last index";
    return `${formatDelta(delta)} vs. ${previous?.toFixed(1) ?? "—"}`;
  };

  const stats: RibbonStat[] = [
    {
      label: "Average health",
      value: summary.current_average_health.toFixed(1),
      valueColor: scoreTextColor(summary.current_average_health),
      sub: deltaSub(summary.average_delta, summary.previous_average_health),
      ...(Math.abs(summary.average_delta ?? 0) >= 0.05
        ? { subColor: deltaColor(summary.average_delta) }
        : {}),
    },
    {
      label: "Hotspot health",
      value: summary.current_hotspot_health.toFixed(1),
      valueColor: scoreTextColor(summary.current_hotspot_health),
      sub: deltaSub(summary.hotspot_delta, summary.previous_hotspot_health),
      ...(Math.abs(summary.hotspot_delta ?? 0) >= 0.05
        ? { subColor: deltaColor(summary.hotspot_delta) }
        : {}),
    },
    {
      label: "Snapshots",
      value: String(data.snapshot_count),
      sub: "rolling window, 50 max",
    },
  ];

  return (
    <div className="flex flex-col gap-6">
      <StatRibbon stats={stats} />

      {data.alerts.length > 0 && (
        <div className="flex flex-col gap-2">
          {data.alerts.map((a, i) => {
            const color =
              a.kind === "declining" ? "var(--color-error)" : "var(--color-warning)";
            return (
              <p key={i} className="flex items-start gap-2 text-sm">
                <AlertTriangle
                  className="mt-0.5 h-4 w-4 shrink-0"
                  style={{ color }}
                  aria-hidden
                />
                <span>
                  <strong className="font-semibold" style={{ color }}>
                    {a.kind === "declining" ? "Declining health." : "Predicted decline."}
                  </strong>{" "}
                  <span className="text-[var(--color-text-secondary)]">{a.message}</span>
                </span>
              </p>
            );
          })}
        </div>
      )}

      {singleSnapshot ? (
        <p className="max-w-[62ch] text-sm text-[var(--color-text-secondary)]">
          One snapshot so far. The trend lines appear once a second one lands. Sync the
          repo, or wait for the next automatic index, and this fills in.
        </p>
      ) : (
        <TrendChart history={[...data.history].reverse()} />
      )}

      <section className="flex flex-col gap-2">
        <h3 className="font-mono text-[10px] uppercase tracking-[0.12em] text-[var(--color-text-tertiary)]">
          Largest score changes since last index
        </h3>
        {data.file_deltas.length === 0 ? (
          <p className="max-w-[62ch] text-sm text-[var(--color-text-secondary)]">
            {singleSnapshot
              ? "Per-file movement compares the last two snapshots, so it appears with the second one."
              : "No file changed score between the last two snapshots."}
          </p>
        ) : (
          <TrendSlopeChart points={data.file_deltas} />
        )}
      </section>
    </div>
  );
}
