"use client";

import { TrendingUp, AlertTriangle, TrendingDown } from "lucide-react";
import { useParams } from "next/navigation";
import useSWR from "swr";

import { Skeleton } from "@repowise-dev/ui/ui/skeleton";
import { TrendChart, deltaColor, formatDelta } from "@repowise-dev/ui/health";

import {
  getHealthTrend,
  type HealthTrendResponse,
} from "@/lib/api/code-health";
import { HealthPageChrome } from "@/components/health/health-page-chrome";

export default function HealthTrendPage() {
  const params = useParams<{ id: string }>();
  const id = params.id;

  const { data, isLoading, error } = useSWR<HealthTrendResponse>(
    `code-health-trend:${id}`,
    () => getHealthTrend(id, 20),
    { revalidateOnFocus: false },
  );

  return (
    <div className="p-4 sm:p-6 space-y-6 max-w-[1400px]">
      <HealthPageChrome
        repoId={id}
        active="trend"
        title="Health trend"
        icon={<TrendingUp className="h-5 w-5 text-emerald-500" />}
        subtitle="KPI history over the most recent indexes. Snapshots are rolling — the latest 50 per repo."
      />

      {isLoading ? (
        <Skeleton className="h-64 w-full rounded-lg" />
      ) : error || !data ? (
        <p className="text-sm text-red-500">Failed to load trend data.</p>
      ) : (
        <>
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
                      ? "border-red-500/40 bg-red-500/5"
                      : "border-amber-500/40 bg-amber-500/5"
                  }`}
                >
                  <AlertTriangle
                    className={`h-4 w-4 mt-0.5 ${a.kind === "declining" ? "text-red-500" : "text-amber-500"}`}
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

          <TrendChart history={[...data.history].reverse()} />

          <section className="space-y-2">
            <h2 className="text-sm font-medium uppercase tracking-wider text-[var(--color-text-tertiary)]">
              Files that moved most since last index
            </h2>
            {data.file_deltas.length === 0 ? (
              <p className="text-sm text-[var(--color-text-tertiary)]">
                No per-file changes between the last two snapshots.
              </p>
            ) : (
              <div className="rounded-lg border border-[var(--color-border-default)] bg-[var(--color-bg-surface)] overflow-hidden">
                <table className="w-full text-sm">
                  <thead className="bg-[var(--color-bg-elevated)] text-xs uppercase tracking-wider text-[var(--color-text-tertiary)]">
                    <tr>
                      <th className="text-left px-3 py-2 font-medium">File</th>
                      <th className="text-right px-3 py-2 font-medium">Before</th>
                      <th className="text-right px-3 py-2 font-medium">After</th>
                      <th className="text-right px-3 py-2 font-medium">Δ</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.file_deltas.map((d) => (
                      <tr
                        key={d.file_path}
                        className="border-t border-[var(--color-border-default)]"
                      >
                        <td className="px-3 py-2 font-mono text-xs text-[var(--color-text-primary)] truncate max-w-[480px]" title={d.file_path}>
                          {d.file_path}
                        </td>
                        <td className="px-3 py-2 text-right tabular-nums text-[var(--color-text-secondary)]">
                          {d.before.toFixed(1)}
                        </td>
                        <td className="px-3 py-2 text-right tabular-nums text-[var(--color-text-secondary)]">
                          {d.after.toFixed(1)}
                        </td>
                        <td className={`px-3 py-2 text-right tabular-nums ${deltaColor(d.delta)}`}>
                          <span className="inline-flex items-center gap-1">
                            {d.delta < 0 ? <TrendingDown className="h-3 w-3" /> : null}
                            {formatDelta(d.delta)}
                          </span>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </section>
        </>
      )}
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
