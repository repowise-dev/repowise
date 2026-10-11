"use client";

import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
  Cell,
} from "recharts";
import { GitCommitHorizontal } from "lucide-react";
import { EmptyState } from "../shared/empty-state";
import type { Hotspot } from "@repowise-dev/types/git";

interface ChurnHistogramProps {
  hotspots: Hotspot[];
}

const BUCKET_COLORS = [
  "var(--color-success)", "var(--color-success)", "var(--color-success)", "var(--color-success)", "var(--color-success)",
  "var(--color-warning)", "var(--color-warning)", "var(--color-accent-fill)",
  "var(--color-error)", "var(--color-error)",
];
const BUCKET_FALLBACK = "var(--color-accent-secondary)";

export function ChurnHistogram({ hotspots }: ChurnHistogramProps) {
  if (!hotspots || hotspots.length === 0) {
    return (
      <EmptyState
        icon={<GitCommitHorizontal className="h-8 w-8" />}
        title="No churn data"
        description="No file churn data is available for this repository."
      />
    );
  }

  const buckets = Array<number>(10).fill(0);
  for (const h of hotspots) {
    const idx = Math.min(9, Math.floor(h.churn_percentile / 10));
    buckets[idx] = (buckets[idx] ?? 0) + 1;
  }

  const data = buckets.map((count, i) => ({
    range: `${i * 10}–${(i + 1) * 10}`,
    count,
  }));

  return (
    <ResponsiveContainer width="100%" height={200}>
      <BarChart data={data} margin={{ top: 8, right: 8, bottom: 0, left: -16 }}>
        <XAxis
          dataKey="range"
          tick={{ fill: "var(--color-text-tertiary)", fontSize: 10 }}
          axisLine={false}
          tickLine={false}
        />
        <YAxis
          tick={{ fill: "var(--color-text-tertiary)", fontSize: 10 }}
          axisLine={false}
          tickLine={false}
          allowDecimals={false}
        />
        <Tooltip
          cursor={{ fill: "var(--color-bg-elevated)" }}
          contentStyle={{
            background: "var(--color-bg-overlay)",
            border: "1px solid var(--color-border-default)",
            borderRadius: "6px",
            fontSize: "12px",
            color: "var(--color-text-primary)",
          }}
          formatter={(value) => [`${typeof value === "number" ? value : 0} files`, "Count"]}
          labelFormatter={(label) => `Churn ${String(label)}%`}
        />
        <Bar dataKey="count" radius={[4, 4, 0, 0]}>
          {data.map((_, i) => (
            <Cell key={i} fill={BUCKET_COLORS[i] ?? BUCKET_FALLBACK} />
          ))}
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  );
}
