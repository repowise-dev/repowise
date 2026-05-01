"use client";

import { PieChart, Pie, Cell, Tooltip, ResponsiveContainer } from "recharts";

interface CoverageDonutProps {
  fresh: number;
  stale: number;
  outdated: number;
}

const COLORS: Record<string, string> = {
  Fresh: "#22c55e",
  Stale: "#eab308",
  Outdated: "#ef4444",
};
const FALLBACK_COLOR = "#22c55e";

export function CoverageDonut({ fresh, stale, outdated }: CoverageDonutProps) {
  const total = fresh + stale + outdated;
  const data = [
    { name: "Fresh", value: fresh },
    { name: "Stale", value: stale },
    { name: "Outdated", value: outdated },
  ].filter((d) => d.value > 0);

  const freshPct = total > 0 ? Math.round((fresh / total) * 100) : 0;

  return (
    <div className="relative flex items-center justify-center">
      <ResponsiveContainer width={200} height={200}>
        <PieChart>
          <Pie
            data={data}
            cx="50%"
            cy="50%"
            innerRadius={60}
            outerRadius={90}
            paddingAngle={2}
            dataKey="value"
            startAngle={90}
            endAngle={-270}
          >
            {data.map((entry) => (
              <Cell
                key={entry.name}
                fill={COLORS[entry.name] ?? FALLBACK_COLOR}
                strokeWidth={0}
              />
            ))}
          </Pie>
          <Tooltip
            contentStyle={{
              background: "var(--color-bg-overlay)",
              border: "1px solid var(--color-border-default)",
              borderRadius: "6px",
              fontSize: "12px",
              color: "var(--color-text-primary)",
            }}
            formatter={(value) => {
              const n = typeof value === "number" ? value : 0;
              return [
                `${n} pages (${total > 0 ? Math.round((n / total) * 100) : 0}%)`,
              ];
            }}
          />
        </PieChart>
      </ResponsiveContainer>
      <div className="absolute flex flex-col items-center pointer-events-none">
        <span className="text-3xl font-bold text-[var(--color-text-primary)] tabular-nums">
          {freshPct}%
        </span>
        <span className="text-xs text-[var(--color-text-tertiary)]">fresh</span>
      </div>
    </div>
  );
}
