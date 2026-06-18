"use client";

import { PieChart, Pie, Cell, Tooltip, ResponsiveContainer } from "recharts";
import { EmptyState } from "../shared/empty-state";

export interface OwnershipDonutSlice {
  name: string;
  value: number;
}

interface OwnershipDonutProps {
  /** Contributors (or any ownership shares) — largest first is not required. */
  slices: OwnershipDonutSlice[];
  /** Max slices before the remainder collapses into an "Others" wedge. */
  maxSlices?: number;
}

const SLICE_COLORS = [
  "var(--color-accent-primary)",
  "var(--color-info)",
  "var(--color-accent-secondary)",
  "var(--color-success)",
  "var(--color-warning)",
  "var(--color-accent-fill)",
];
const OTHERS_COLOR = "var(--color-text-tertiary)";

/**
 * Ownership concentration as a donut — shows at a glance whether a file is
 * owned by one person (bus-factor risk) or shared. Replaces the plain
 * "Top authors" count list.
 */
export function OwnershipDonut({ slices, maxSlices = 5 }: OwnershipDonutProps) {
  const sorted = [...slices].filter((s) => s.value > 0).sort((a, b) => b.value - a.value);
  const total = sorted.reduce((s, d) => s + d.value, 0);

  if (total === 0) {
    return (
      <EmptyState title="No author data" description="Author attribution appears after the next git sync." />
    );
  }

  const head = sorted.slice(0, maxSlices);
  const restTotal = sorted.slice(maxSlices).reduce((s, d) => s + d.value, 0);
  const data = restTotal > 0 ? [...head, { name: "Others", value: restTotal }] : head;
  const top = sorted[0]!;

  return (
    <div className="flex items-center gap-4">
      <div className="relative flex shrink-0 items-center justify-center">
        <ResponsiveContainer width={140} height={140}>
          <PieChart>
            <Pie
              data={data}
              cx="50%"
              cy="50%"
              innerRadius={40}
              outerRadius={62}
              paddingAngle={2}
              dataKey="value"
              startAngle={90}
              endAngle={-270}
            >
              {data.map((entry, i) => (
                <Cell
                  key={entry.name}
                  fill={
                    entry.name === "Others"
                      ? OTHERS_COLOR
                      : SLICE_COLORS[i % SLICE_COLORS.length] ?? OTHERS_COLOR
                  }
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
              formatter={(value, name) => {
                const n = typeof value === "number" ? value : 0;
                return [`${n} commits (${Math.round((n / total) * 100)}%)`, name];
              }}
            />
          </PieChart>
        </ResponsiveContainer>
        <div className="pointer-events-none absolute flex flex-col items-center">
          <span className="text-lg font-bold tabular-nums text-[var(--color-text-primary)]">
            {Math.round((top.value / total) * 100)}%
          </span>
          <span className="text-[10px] text-[var(--color-text-tertiary)]">top owner</span>
        </div>
      </div>
      <ul className="min-w-0 flex-1 space-y-1 text-xs">
        {data.map((entry, i) => (
          <li key={entry.name} className="flex items-center gap-2">
            <span
              className="h-2 w-2 shrink-0 rounded-full"
              style={{
                backgroundColor:
                  entry.name === "Others"
                    ? OTHERS_COLOR
                    : SLICE_COLORS[i % SLICE_COLORS.length] ?? OTHERS_COLOR,
              }}
            />
            <span className="truncate text-[var(--color-text-primary)]">{entry.name}</span>
            <span className="ml-auto shrink-0 tabular-nums text-[var(--color-text-tertiary)]">
              {Math.round((entry.value / total) * 100)}%
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}
