"use client";

import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
} from "recharts";

interface ContributorBarProps {
  owners: Array<{ name: string; email: string; file_count: number; pct: number }>;
}

export function ContributorBar({ owners }: ContributorBarProps) {
  const data = owners.slice(0, 5).map((o) => ({
    name: o.name.split(" ")[0] ?? o.name,
    files: o.file_count ?? 0,
  }));

  if (data.length === 0) return null;

  return (
    <ResponsiveContainer width="100%" height={160}>
      <BarChart
        layout="vertical"
        data={data}
        margin={{ top: 0, right: 16, bottom: 0, left: 0 }}
      >
        <XAxis type="number" hide />
        <YAxis
          type="category"
          dataKey="name"
          width={72}
          tick={{ fill: "var(--color-text-secondary)", fontSize: 12 }}
          axisLine={false}
          tickLine={false}
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
          formatter={(value: number) => [value, "files"]}
        />
        <Bar dataKey="files" fill="var(--color-accent-primary)" radius={[0, 4, 4, 0]} />
      </BarChart>
    </ResponsiveContainer>
  );
}
