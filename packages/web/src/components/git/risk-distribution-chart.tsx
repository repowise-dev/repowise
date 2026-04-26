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
import type { HotspotResponse } from "@/lib/api/types";

interface RiskDistributionChartProps {
  hotspots: HotspotResponse[];
}

function computeRiskScore(h: HotspotResponse): number {
  const churn = h.churn_percentile / 100;
  const busFactor = h.bus_factor <= 1 ? 1 : h.bus_factor === 2 ? 0.5 : 0;
  const trend = Math.min(1, (h.temporal_hotspot_score ?? 0) / 10);
  return Math.round((churn * 0.4 + busFactor * 0.35 + trend * 0.25) * 100);
}

function riskColor(score: number): string {
  if (score >= 70) return "#ef4444";
  if (score >= 45) return "#f59520";
  if (score >= 25) return "#eab308";
  return "#22c55e";
}

export function RiskDistributionChart({ hotspots }: RiskDistributionChartProps) {
  const data = useMemo(() => {
    return hotspots
      .map((h) => ({
        name: h.file_path.split("/").pop() ?? h.file_path,
        fullPath: h.file_path,
        risk: computeRiskScore(h),
        churn: Math.round(h.churn_percentile),
        busFactor: h.bus_factor,
      }))
      .sort((a, b) => b.risk - a.risk)
      .slice(0, 30);
  }, [hotspots]);

  if (data.length === 0) return null;

  const avgRisk = Math.round(data.reduce((s, d) => s + d.risk, 0) / data.length);

  return (
    <div className="w-full">
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
            formatter={(value: number) => [`${value}`, "Risk Score"]}
            labelFormatter={(label: string) => label}
          />
          <ReferenceLine
            x={avgRisk}
            stroke="var(--color-text-tertiary)"
            strokeDasharray="4 4"
            label={{ value: `avg ${avgRisk}`, position: "top", fontSize: 9, fill: "var(--color-text-tertiary)" }}
          />
          <Bar dataKey="risk" radius={[0, 4, 4, 0]}>
            {data.map((entry, idx) => (
              <Cell key={idx} fill={riskColor(entry.risk)} fillOpacity={0.85} />
            ))}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}
