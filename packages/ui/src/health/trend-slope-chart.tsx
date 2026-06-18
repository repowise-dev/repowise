"use client";

import { useMemo, useState } from "react";
import { scoreBand } from "./tokens";

export interface TrendSlopePoint {
  file_path: string;
  before: number;
  after: number;
  delta: number;
}

export interface TrendSlopeChartProps {
  points: TrendSlopePoint[];
  height?: number;
  /** Cap the number of slopes drawn (largest |delta| first). */
  max?: number;
}

const BAND_STROKE: Record<string, string> = {
  critical: "var(--color-error)",
  poor: "var(--color-warning)",
  fair: "var(--color-caution)",
  good: "var(--color-success)",
};

/**
 * Slope chart for "largest score changes" — each file is a line from its
 * before-score to its after-score across two snapshot columns. Declines drop,
 * improvements rise; colour follows the after-score health band. Replaces the
 * Before/After/Δ table (movement is read positionally, not parsed row by row).
 */
export function TrendSlopeChart({ points, height = 320, max = 18 }: TrendSlopeChartProps) {
  const [hovered, setHovered] = useState<TrendSlopePoint | null>(null);

  const data = useMemo(
    () =>
      [...points]
        .sort((a, b) => Math.abs(b.delta) - Math.abs(a.delta))
        .slice(0, max),
    [points, max],
  );

  if (data.length === 0) {
    return (
      <div className="rounded-lg border border-dashed border-[var(--color-border-default)] bg-[var(--color-bg-surface)] p-6 text-sm text-[var(--color-text-tertiary)]">
        No per-file score changes to plot yet.
      </div>
    );
  }

  const W = 640;
  const H = height;
  const padT = 16;
  const padB = 28;
  const xBefore = 140;
  const xAfter = W - 140;
  const plotH = H - padT - padB;

  // Fixed 0–10 health domain so the slope angle is meaningful across renders.
  const yScale = (score: number) => padT + ((10 - Math.max(0, Math.min(10, score))) / 10) * plotH;

  return (
    <div className="relative rounded-lg border border-[var(--color-border-default)] bg-[var(--color-bg-surface)] p-3">
      <svg viewBox={`0 0 ${W} ${H}`} width="100%" height={H} role="img" aria-label="Score change slope chart">
        {/* Axis columns */}
        <line x1={xBefore} y1={padT} x2={xBefore} y2={H - padB} stroke="currentColor" strokeOpacity={0.15} />
        <line x1={xAfter} y1={padT} x2={xAfter} y2={H - padB} stroke="currentColor" strokeOpacity={0.15} />
        <text x={xBefore} y={H - 8} fontSize={11} textAnchor="middle" fill="currentColor" opacity={0.6}>
          Before
        </text>
        <text x={xAfter} y={H - 8} fontSize={11} textAnchor="middle" fill="currentColor" opacity={0.6}>
          After
        </text>

        {data.map((p) => {
          const y1 = yScale(p.before);
          const y2 = yScale(p.after);
          const stroke = BAND_STROKE[scoreBand(p.after)] ?? "var(--color-caution)";
          const isHovered = hovered?.file_path === p.file_path;
          const name = p.file_path.split("/").pop() ?? p.file_path;
          return (
            <g
              key={p.file_path}
              onMouseEnter={() => setHovered(p)}
              onMouseLeave={() => setHovered(null)}
              className="cursor-default"
            >
              <line
                x1={xBefore}
                y1={y1}
                x2={xAfter}
                y2={y2}
                stroke={stroke}
                strokeWidth={isHovered ? 2.5 : 1.5}
                strokeOpacity={hovered && !isHovered ? 0.25 : 0.8}
              />
              <circle cx={xBefore} cy={y1} r={isHovered ? 4 : 3} fill={stroke} fillOpacity={0.9} />
              <circle cx={xAfter} cy={y2} r={isHovered ? 4 : 3} fill={stroke} fillOpacity={0.9} />
              <text x={xBefore - 8} y={y1 + 3} fontSize={10} textAnchor="end" fill="currentColor" opacity={isHovered ? 0.9 : 0.55}>
                {p.before.toFixed(1)}
              </text>
              <text x={xAfter + 8} y={y2 + 3} fontSize={10} textAnchor="start" fill="currentColor" opacity={isHovered ? 0.9 : 0.55}>
                {name}
              </text>
            </g>
          );
        })}
      </svg>
      {hovered ? (
        <div className="pointer-events-none absolute left-2 top-2 rounded-md border border-[var(--color-border-default)] bg-[var(--color-bg-elevated)] px-2 py-1 text-xs shadow-md">
          <span className="font-mono text-[var(--color-text-primary)]">{hovered.file_path}</span>
          <span className="ml-2 tabular-nums text-[var(--color-text-tertiary)]">
            {hovered.before.toFixed(1)} → {hovered.after.toFixed(1)} ({hovered.delta >= 0 ? "+" : ""}
            {hovered.delta.toFixed(1)})
          </span>
        </div>
      ) : null}
    </div>
  );
}
