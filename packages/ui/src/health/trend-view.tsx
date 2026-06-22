"use client";

/**
 * Trend view — KPI history over recent indexes. Handles the single-snapshot
 * case explicitly (no misleading flat chart) and renders per-file score
 * movement as a slope chart. Trend data is fetched once by the host and passed
 * in, so it isn't double-fetched alongside the KPI sparklines.
 */

import { AlertTriangle, TrendingUp } from "lucide-react";
import type { HealthTrendResponse } from "@repowise-dev/types/health";

import { Skeleton } from "../ui/skeleton";
import { MetricCard } from "../shared/metric-card";
import { EmptyState } from "../shared/empty-state";

import { TrendChart } from "./trend-chart";
import { TrendSlopeChart } from "./trend-slope-chart";
import { deltaColor, formatDelta } from "./tokens";

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
      <EmptyState
        title="Couldn't load trend data"
        description="The trend endpoint returned an error. Try refreshing."
      />
    );
  }

  const singleSnapshot = data.snapshot_count <= 1;

  return (
    <div className="space-y-6">
      <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
        <DeltaMetric
          label="Average health"
          current={data.summary.current_average_health}
          previous={data.summary.previous_average_health}
          delta={data.summary.average_delta}
        />
        <DeltaMetric
          label="Hotspot health"
          current={data.summary.current_hotspot_health}
          previous={data.summary.previous_hotspot_health}
          delta={data.summary.hotspot_delta}
        />
        <MetricCard
          label="Snapshots"
          value={data.snapshot_count}
          distBar={
            <span className="text-xs text-[var(--color-text-tertiary)]">
              rolling window: 50 max
            </span>
          }
        />
      </div>

      {data.alerts.length > 0 ? (
        <div className="space-y-2">
          {data.alerts.map((a, i) => (
            <div
              key={i}
              className={`rounded-lg border p-3 flex items-start gap-2 ${
                a.kind === "declining"
                  ? "border-[var(--color-error)]/40 bg-[var(--color-error)]/5"
                  : "border-[var(--color-warning)]/40 bg-[var(--color-warning)]/5"
              }`}
            >
              <AlertTriangle
                className={`h-4 w-4 mt-0.5 ${a.kind === "declining" ? "text-[var(--color-error)]" : "text-[var(--color-warning)]"}`}
              />
              <div className="text-sm">
                <p className="font-medium text-[var(--color-text-primary)]">
                  {a.kind === "declining" ? "Declining health" : "Predicted decline"}
                </p>
                <p className="text-xs text-[var(--color-text-secondary)]">{a.message}</p>
              </div>
            </div>
          ))}
        </div>
      ) : null}

      {singleSnapshot ? (
        <EmptyState
          icon={<TrendingUp className="h-6 w-6" />}
          title="One snapshot so far"
          description="The trend lines appear once a second snapshot lands. Run repowise update (or wait for the next sync) and this page fills in."
        />
      ) : (
        <TrendChart history={[...data.history].reverse()} />
      )}

      <section className="space-y-2">
        <h2 className="text-sm font-medium uppercase tracking-wider text-[var(--color-text-tertiary)]">
          Largest score changes since last index
        </h2>
        {data.file_deltas.length === 0 ? (
          <EmptyState
            {...(singleSnapshot
              ? {
                  title: "No comparison available yet",
                  description: "Per-file deltas compare the last two snapshots.",
                }
              : { title: "No per-file changes between the last two snapshots" })}
          />
        ) : (
          <TrendSlopeChart points={data.file_deltas} />
        )}
      </section>
    </div>
  );
}

function DeltaMetric({
  label,
  current,
  previous,
  delta,
}: {
  label: string;
  current: number;
  previous: number | null;
  delta: number | null;
}) {
  return (
    <MetricCard
      label={label}
      value={
        <>
          {current.toFixed(1)}
          <span className="text-base font-normal text-[var(--color-text-secondary)]">
            /10
          </span>
        </>
      }
      distBar={
        <span className={`text-xs tabular-nums ${deltaColor(delta)}`}>
          {delta == null
            ? "no prior snapshot"
            : `${formatDelta(delta)} vs. ${previous?.toFixed(1) ?? "—"}`}
        </span>
      }
    />
  );
}
