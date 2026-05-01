"use client";

import {
  BarChart,
  Bar,
  XAxis,
  Tooltip,
  ResponsiveContainer,
} from "recharts";
import { truncatePath } from "@repowise/ui/lib/format";
import type { HotspotResponse } from "@/lib/api/types";

interface BusFactorPanelProps {
  hotspots: HotspotResponse[];
}

export function BusFactorPanel({ hotspots }: BusFactorPanelProps) {
  const high = hotspots.filter((h) => h.bus_factor >= 3).length;
  const medium = hotspots.filter((h) => h.bus_factor === 2).length;
  const low = hotspots.filter((h) => h.bus_factor <= 1).length;
  const total = hotspots.length || 1;

  const data = [{ high, medium, low }];

  const riskFiles = hotspots
    .filter((h) => h.bus_factor <= 1)
    .sort((a, b) => b.commit_count_90d - a.commit_count_90d)
    .slice(0, 5);

  return (
    <div className="space-y-4">
      {/* Stacked bar */}
      <div>
        <div className="flex items-center justify-between text-xs text-[var(--color-text-tertiary)] mb-2">
          <span>Bus Factor Distribution</span>
          <span>{hotspots.length} files</span>
        </div>
        <ResponsiveContainer width="100%" height={36}>
          <BarChart layout="vertical" data={data} margin={{ top: 0, right: 0, bottom: 0, left: 0 }}>
            <XAxis type="number" hide domain={[0, total]} />
            <Tooltip
              cursor={false}
              contentStyle={{
                background: "var(--color-bg-overlay)",
                border: "1px solid var(--color-border-default)",
                borderRadius: "6px",
                fontSize: "12px",
                color: "var(--color-text-primary)",
              }}
              formatter={(value: number, name: string) => {
                const label = name === "high" ? "Safe (≥3)" : name === "medium" ? "Warning (2)" : "Risk (≤1)";
                return [`${value} files`, label];
              }}
            />
            <Bar dataKey="high" stackId="a" fill="#22c55e" radius={[4, 0, 0, 4]} />
            <Bar dataKey="medium" stackId="a" fill="#eab308" />
            <Bar dataKey="low" stackId="a" fill="#ef4444" radius={[0, 4, 4, 0]} />
          </BarChart>
        </ResponsiveContainer>
        <div className="flex items-center gap-4 mt-2 text-[10px] text-[var(--color-text-tertiary)]">
          <span className="flex items-center gap-1">
            <span className="inline-block h-2 w-2 rounded-sm bg-green-500" /> Safe (≥3): {high}
          </span>
          <span className="flex items-center gap-1">
            <span className="inline-block h-2 w-2 rounded-sm bg-yellow-500" /> Warning (2): {medium}
          </span>
          <span className="flex items-center gap-1">
            <span className="inline-block h-2 w-2 rounded-sm bg-red-500" /> Risk (≤1): {low}
          </span>
        </div>
      </div>

      {/* At-risk files list */}
      {riskFiles.length > 0 && (
        <div>
          <p className="text-xs font-medium text-[var(--color-text-tertiary)] uppercase tracking-wider mb-2">
            Highest Risk Files
          </p>
          <div className="space-y-1.5">
            {riskFiles.map((f) => (
              <div
                key={f.file_path}
                className="flex items-center justify-between text-xs rounded-md px-2 py-1.5 bg-[var(--color-bg-elevated)]"
              >
                <span className="font-mono text-[var(--color-text-primary)] truncate flex-1 min-w-0" title={f.file_path}>
                  {truncatePath(f.file_path)}
                </span>
                <span className="text-[var(--color-text-tertiary)] tabular-nums ml-2 shrink-0">
                  {f.contributor_count} contributor{f.contributor_count !== 1 ? "s" : ""}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
