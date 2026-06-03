"use client";

import { useState } from "react";
import {
  BarChart,
  Bar,
  XAxis,
  Tooltip,
  ResponsiveContainer,
} from "recharts";
import { Users } from "lucide-react";
import { EmptyState } from "../shared/empty-state";
import { truncatePath } from "../lib/format";
import type { Hotspot } from "@repowise-dev/types/git";

interface BusFactorPanelProps {
  hotspots: Hotspot[];
  /** Number of risk files shown before the "show more" toggle. */
  riskPreviewCount?: number;
}

export function BusFactorPanel({ hotspots, riskPreviewCount = 5 }: BusFactorPanelProps) {
  const [showAllRisk, setShowAllRisk] = useState(false);
  if (!hotspots || hotspots.length === 0) {
    return (
      <EmptyState
        icon={<Users className="h-8 w-8" />}
        title="No bus factor data"
        description="No ownership data is available for this repository."
      />
    );
  }

  const high = hotspots.filter((h) => h.bus_factor >= 3).length;
  const medium = hotspots.filter((h) => h.bus_factor === 2).length;
  const low = hotspots.filter((h) => h.bus_factor <= 1).length;
  const total = hotspots.length || 1;

  const data = [{ high, medium, low }];

  const allRiskFiles = hotspots
    .filter((h) => h.bus_factor <= 1)
    .sort((a, b) => b.commit_count_90d - a.commit_count_90d);
  const riskFiles = showAllRisk
    ? allRiskFiles
    : allRiskFiles.slice(0, riskPreviewCount);
  const hiddenRisk = allRiskFiles.length - riskFiles.length;

  return (
    <div className="space-y-4">
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
              formatter={(value, name) => {
                const n = typeof value === "number" ? value : 0;
                const label = name === "high" ? "Safe (≥3)" : name === "medium" ? "Warning (2)" : "Risk (≤1)";
                return [`${n} files`, label];
              }}
            />
            <Bar dataKey="high" stackId="a" fill="var(--color-success)" radius={[4, 0, 0, 4]} />
            <Bar dataKey="medium" stackId="a" fill="var(--color-warning)" />
            <Bar dataKey="low" stackId="a" fill="var(--color-error)" radius={[0, 4, 4, 0]} />
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

      {riskFiles.length > 0 && (
        <div>
          <p className="text-xs font-medium text-[var(--color-text-tertiary)] uppercase tracking-wider mb-2 flex items-center justify-between">
            <span>Highest Risk Files</span>
            <span className="font-normal normal-case tracking-normal text-[10px]">
              {riskFiles.length} of {allRiskFiles.length}
            </span>
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
          {(hiddenRisk > 0 || showAllRisk) && (
            <button
              type="button"
              onClick={() => setShowAllRisk((v) => !v)}
              className="mt-2 text-[10px] text-[var(--color-accent-primary)] hover:underline"
            >
              {showAllRisk ? "Show fewer" : `Show ${hiddenRisk} more`}
            </button>
          )}
        </div>
      )}
    </div>
  );
}
