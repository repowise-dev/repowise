"use client";

/**
 * Trend tab — KPI history over recent indexes. Handles the single-snapshot
 * case explicitly (no misleading flat chart) and renders per-file deltas on
 * a ResponsiveTable.
 */

import { AlertTriangle, TrendingDown, TrendingUp } from "lucide-react";
import useSWR from "swr";

import { Skeleton } from "@repowise-dev/ui/ui/skeleton";
import { TrendChart, deltaColor, formatDelta } from "@repowise-dev/ui/health";
import { EmptyState } from "@repowise-dev/ui/shared/empty-state";
import { ResponsiveTable, type ResponsiveColumn } from "@repowise-dev/ui/shared";

import {
  getHealthTrend,
  type HealthTrendResponse,
} from "@/lib/api/code-health";

type FileDelta = HealthTrendResponse["file_deltas"][number];

export function TrendTab({ repoId: id }: { repoId: string }) {
  const { data, isLoading, error } = useSWR<HealthTrendResponse>(
    `code-health-trend:${id}`,
    () => getHealthTrend(id, 20),
    { revalidateOnFocus: false },
  );

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

  const deltaColumns: ResponsiveColumn<FileDelta>[] = [
    {
      key: "file_path",
      header: "File",
      priority: 1,
      render: (d) => (
        <span
          className="font-mono text-xs text-[var(--color-text-primary)] truncate max-w-[420px] inline-block"
          title={d.file_path}
        >
          {d.file_path}
        </span>
      ),
    },
    {
      key: "before",
      header: "Before",
      priority: 2,
      align: "right",
      render: (d) => (
        <span className="tabular-nums text-[var(--color-text-secondary)]">
          {d.before.toFixed(1)}
        </span>
      ),
    },
    {
      key: "after",
      header: "After",
      priority: 2,
      align: "right",
      render: (d) => (
        <span className="tabular-nums text-[var(--color-text-secondary)]">
          {d.after.toFixed(1)}
        </span>
      ),
    },
    {
      key: "delta",
      header: "Δ",
      priority: 1,
      align: "right",
      render: (d) => (
        <span className={`tabular-nums inline-flex items-center gap-1 ${deltaColor(d.delta)}`}>
          {d.delta < 0 ? <TrendingDown className="h-3 w-3" /> : null}
          {formatDelta(d.delta)}
        </span>
      ),
    },
  ];

  return (
    <div className="space-y-6">
      <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
        <DeltaCard
          label="Average health"
          current={data.summary.current_average_health}
          previous={data.summary.previous_average_health}
          delta={data.summary.average_delta}
        />
        <DeltaCard
          label="Hotspot health"
          current={data.summary.current_hotspot_health}
          previous={data.summary.previous_hotspot_health}
          delta={data.summary.hotspot_delta}
        />
        <div className="rounded-lg border border-[var(--color-border-default)] bg-[var(--color-bg-surface)] p-4">
          <p className="text-xs font-medium text-[var(--color-text-tertiary)] uppercase tracking-wider">
            Snapshots
          </p>
          <p className="mt-1 text-2xl font-bold text-[var(--color-text-primary)] tabular-nums">
            {data.snapshot_count}
          </p>
          <p className="text-[11px] text-[var(--color-text-tertiary)] mt-1">
            rolling window: 50 max
          </p>
        </div>
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
            title={
              singleSnapshot
                ? "No comparison available yet"
                : "No per-file changes between the last two snapshots"
            }
            description={
              singleSnapshot
                ? "Per-file deltas compare the last two snapshots."
                : undefined
            }
          />
        ) : (
          <ResponsiveTable
            columns={deltaColumns}
            rows={data.file_deltas}
            rowKey={(d) => d.file_path}
            stacked="sm"
          />
        )}
      </section>
    </div>
  );
}

function DeltaCard({
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
    <div className="rounded-lg border border-[var(--color-border-default)] bg-[var(--color-bg-surface)] p-4">
      <p className="text-xs font-medium text-[var(--color-text-tertiary)] uppercase tracking-wider">
        {label}
      </p>
      <p className="mt-1 text-2xl font-bold text-[var(--color-text-primary)] tabular-nums">
        {current.toFixed(1)}
        <span className="text-base font-normal text-[var(--color-text-secondary)]">/10</span>
      </p>
      <p className={`text-[11px] tabular-nums mt-0.5 ${deltaColor(delta)}`}>
        {delta == null
          ? "no prior snapshot"
          : `${formatDelta(delta)} vs. ${previous?.toFixed(1) ?? "—"}`}
      </p>
    </div>
  );
}
