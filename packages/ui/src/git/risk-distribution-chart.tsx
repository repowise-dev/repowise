"use client";

import { useMemo } from "react";
import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
  Cell,
  ReferenceLine,
} from "recharts";
import type { Hotspot } from "@repowise-dev/types/git";
import { riskInk } from "../health/tokens";

interface RiskDistributionChartProps {
  hotspots: Hotspot[];
  /**
   * Maximum bars to render before the chart becomes illegible. The count
   * is surfaced to the user via the caller — this prop only controls the
   * visual cap, not what data the parent fetched.
   */
  maxBars?: number;
}

function computeTriageIndex(h: Hotspot): number {
  const churn = h.churn_percentile / 100;
  const busFactor = h.bus_factor <= 1 ? 1 : h.bus_factor === 2 ? 0.5 : 0;
  const trend = Math.min(1, (h.temporal_hotspot_score ?? 0) / 10);
  return Math.round((churn * 0.4 + busFactor * 0.35 + trend * 0.25) * 100);
}

function triageColor(score: number): string {
  return riskInk(score / 100);
}

export function RiskDistributionChart({ hotspots, maxBars = 30 }: RiskDistributionChartProps) {
  const data = useMemo(() => {
    return hotspots
      .map((h) => ({
        name: h.file_path.split("/").pop() ?? h.file_path,
        fullPath: h.file_path,
        triageIndex: computeTriageIndex(h),
        churn: Math.round(h.churn_percentile),
        busFactor: h.bus_factor,
      }))
      .sort((a, b) => b.triageIndex - a.triageIndex)
      .slice(0, maxBars);
  }, [hotspots, maxBars]);

  if (data.length === 0) return null;

  const averageIndex = Math.round(
    data.reduce((sum, row) => sum + row.triageIndex, 0) / data.length,
  );
  const truncated = hotspots.length > data.length;

  return (
    <div className="w-full">
      <p className="mb-1 text-[10px] text-[var(--color-text-tertiary)]">
        Triage index (0–100 heuristic): 40% churn percentile + 35% bus-factor tier +
        25% temporal activity. Uncalibrated; not a probability.
      </p>
      {truncated && (
        <p className="text-[10px] text-[var(--color-text-tertiary)] mb-1 text-right tabular-nums">
          Showing top {data.length} of {hotspots.length.toLocaleString()} by triage index
        </p>
      )}
      <ResponsiveContainer width="100%" height={Math.max(200, data.length * 22 + 40)}>
        <BarChart
          data={data}
          layout="vertical"
          margin={{ top: 5, right: 20, bottom: 5, left: 0 }}
          barSize={14}
        >
          <XAxis
            type="number"
            domain={[0, 100]}
            tick={{ fontSize: 10, fill: "var(--color-text-tertiary)" }}
            axisLine={false}
            tickLine={false}
          />
          <YAxis
            type="category"
            dataKey="name"
            width={120}
            tick={{ fontSize: 10, fill: "var(--color-text-secondary)" }}
            axisLine={false}
            tickLine={false}
          />
          <Tooltip
            contentStyle={{
              background: "var(--color-bg-elevated)",
              border: "1px solid var(--color-border-default)",
              borderRadius: 8,
              fontSize: 11,
              color: "var(--color-text-primary)",
            }}
            formatter={(value) => [`${typeof value === "number" ? value : 0}`, "Triage index"]}
            labelFormatter={(label) => String(label)}
          />
          <ReferenceLine
            x={averageIndex}
            stroke="var(--color-text-tertiary)"
            strokeDasharray="4 4"
            label={{ value: `avg ${averageIndex}`, position: "top", fontSize: 9, fill: "var(--color-text-tertiary)" }}
          />
          <Bar dataKey="triageIndex" radius={[0, 4, 4, 0]}>
            {data.map((entry, idx) => (
              <Cell key={idx} fill={triageColor(entry.triageIndex)} fillOpacity={0.85} />
            ))}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}
