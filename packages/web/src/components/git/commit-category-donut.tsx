"use client";

import { PieChart, Pie, Cell, Tooltip, ResponsiveContainer } from "recharts";

interface CommitCategoryDonutProps {
  categories: Record<string, number>;
}

const CATEGORY_COLORS: Record<string, string> = {
  feature: "#5b9cf6",
  fix: "#ef4444",
  refactor: "#a855f7",
  dependency: "#f59520",
};

const CATEGORY_ORDER = ["feature", "fix", "refactor", "dependency"];

export function CommitCategoryDonut({ categories }: CommitCategoryDonutProps) {
  const data = CATEGORY_ORDER.map((key) => ({
    name: key.charAt(0).toUpperCase() + key.slice(1),
    value: categories[key] || 0,
    key,
  })).filter((d) => d.value > 0);

  const total = data.reduce((sum, d) => sum + d.value, 0);
  if (total === 0) return null;

  const dominant = data.reduce((a, b) => (a.value >= b.value ? a : b));

  return (
    <div className="relative flex items-center justify-center">
      <ResponsiveContainer width={180} height={180}>
        <PieChart>
          <Pie
            data={data}
            cx="50%"
            cy="50%"
            innerRadius={50}
            outerRadius={75}
            paddingAngle={2}
            dataKey="value"
            startAngle={90}
            endAngle={-270}
          >
            {data.map((entry) => (
              <Cell
                key={entry.key}
                fill={CATEGORY_COLORS[entry.key] || "#8b5cf6"}
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
            formatter={(value: number, name: string) => [
              `${value} commits (${Math.round((value / total) * 100)}%)`,
              name,
            ]}
          />
        </PieChart>
      </ResponsiveContainer>
      <div className="absolute flex flex-col items-center pointer-events-none">
        <span className="text-lg font-bold text-[var(--color-text-primary)] tabular-nums">
          {Math.round((dominant.value / total) * 100)}%
        </span>
        <span className="text-[10px] text-[var(--color-text-tertiary)] capitalize">
          {dominant.name}
        </span>
      </div>
    </div>
  );
}
