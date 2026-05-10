"use client";

import { Card, CardContent, CardHeader, CardTitle } from "../ui/card";
import { formatCost } from "../lib/format";

export interface CostHeatmapProps {
  /** Operation- or model-grouped cost data. */
  groups: Array<{ group: string; cost_usd: number; calls: number }>;
  title?: string;
  emptyHint?: string;
}

/**
 * Treemap-style cost visualizer using flexbox basis. Lightweight stand-in for
 * the per-directory heatmap until backend exposes file_path grouping.
 */
export function CostHeatmap({ groups, title = "Cost concentration", emptyHint }: CostHeatmapProps) {
  const sorted = [...groups].filter((g) => g.cost_usd > 0).sort((a, b) => b.cost_usd - a.cost_usd);
  const total = sorted.reduce((s, g) => s + g.cost_usd, 0);

  if (sorted.length === 0 || total === 0) {
    return (
      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-sm">{title}</CardTitle>
        </CardHeader>
        <CardContent className="pt-0">
          <p className="text-xs text-[var(--color-text-secondary)] py-6 text-center">
            {emptyHint ?? "No cost data to visualize yet."}
          </p>
        </CardContent>
      </Card>
    );
  }

  const top = sorted.slice(0, 12);
  const max = top[0]?.cost_usd ?? 1;
  const min = top[top.length - 1]?.cost_usd ?? 0;
  const range = Math.max(1e-9, max - min);

  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-sm">{title}</CardTitle>
      </CardHeader>
      <CardContent className="pt-0">
        <div className="flex flex-wrap gap-1.5">
          {top.map((g) => {
            const pctOfMax = g.cost_usd / max;
            const intensity = 0.18 + 0.7 * ((g.cost_usd - min) / range);
            const basis = `${Math.max(80, Math.round(pctOfMax * 280))}px`;
            const height = `${Math.max(48, Math.round(pctOfMax * 96))}px`;
            return (
              <div
                key={g.group}
                className="rounded-md border border-[var(--color-border-default)] p-2 flex flex-col justify-between transition-colors hover:border-[var(--color-accent-primary)]"
                style={{
                  flexBasis: basis,
                  flexGrow: 1,
                  height,
                  backgroundColor: `rgba(244, 63, 94, ${intensity.toFixed(3)})`,
                }}
                title={`${g.group} — ${formatCost(g.cost_usd)} (${g.calls} calls)`}
              >
                <span className="text-[10px] font-mono text-white/90 truncate">{g.group}</span>
                <span className="text-[10px] text-white/80 tabular-nums">{formatCost(g.cost_usd)}</span>
              </div>
            );
          })}
        </div>
      </CardContent>
    </Card>
  );
}
