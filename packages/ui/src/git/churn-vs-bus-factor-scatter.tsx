"use client";

import * as React from "react";
import { cn } from "../lib/cn";

export interface ScatterHotspot {
  file_path: string;
  /** 0–100 percentile rank (post-normalisation in the HTTP layer). */
  churn_percentile: number;
  commit_count_90d: number;
  bus_factor: number;
  primary_owner?: string | null;
}

interface ChurnVsBusFactorScatterProps {
  hotspots: ScatterHotspot[];
  className?: string;
  onSelect?: (filePath: string) => void;
  height?: number;
  /**
   * Maximum filename labels rendered on the danger-zone points. Keeps the
   * chart legible while still telling the user *which* files are fragile.
   */
  maxDangerLabels?: number;
}

/**
 * Bubble scatter: x = churn percentile (urgency), y = inverse bus factor
 * (knowledge concentration). Top-right quadrant = "fragile and on fire."
 *
 * Implemented as inline SVG (no recharts dep) so it stays cheap and the
 * component can be reused in `packages/ui` without bumping the chart lib
 * version everywhere.
 */
export function ChurnVsBusFactorScatter({
  hotspots,
  className,
  onSelect,
  height = 240,
  maxDangerLabels = 5,
}: ChurnVsBusFactorScatterProps) {
  const padding = { top: 12, right: 12, bottom: 28, left: 36 };
  const width = 480;
  const innerW = width - padding.left - padding.right;
  const innerH = height - padding.top - padding.bottom;

  const points = React.useMemo(() => {
    return hotspots.map((h) => {
      // Defensive coverage: tolerate either 0–1 (legacy) or 0–100 callers.
      const raw = h.churn_percentile ?? 0;
      const churn = raw <= 1 ? raw * 100 : raw;
      const x = Math.max(0, Math.min(100, churn));
      // bus_factor of 1 → top of chart (riskiest); cap at 8 for visual scale.
      const busClamped = Math.max(1, Math.min(8, h.bus_factor || 1));
      const y = ((8 - busClamped) / 7) * 100;
      // Bubble size by commit_count_90d (sqrt for area-ish scaling).
      const r = 4 + Math.min(14, Math.sqrt(h.commit_count_90d ?? 0));
      const inDanger = x >= 50 && y >= 50;
      return { ...h, _x: x, _y: y, _r: r, _danger: inDanger };
    });
  }, [hotspots]);

  const dangerPoints = React.useMemo(
    () =>
      points
        .filter((p) => p._danger)
        .sort((a, b) => b._x + b._y - (a._x + a._y))
        .slice(0, maxDangerLabels),
    [points, maxDangerLabels],
  );
  const dangerCount = points.filter((p) => p._danger).length;
  const allChurnZero = points.every((p) => p._x === 0);

  if (points.length === 0) {
    return (
      <div
        className={cn(
          "rounded-md border border-dashed border-[var(--color-border-default)] p-4 text-center text-xs text-[var(--color-text-tertiary)]",
          className,
        )}
      >
        No hotspots yet.
      </div>
    );
  }

  // When every file has churn=0, the scatter degenerates to a vertical line
  // and conveys nothing — give the user something useful instead.
  if (allChurnZero) {
    return (
      <div
        className={cn(
          "rounded-md border border-dashed border-[var(--color-border-default)] p-4 text-xs text-[var(--color-text-tertiary)] space-y-1",
          className,
        )}
      >
        <p className="font-medium text-[var(--color-text-secondary)]">
          Not enough churn variation to plot.
        </p>
        <p>
          The repo&apos;s churn percentile is uniform — likely a fresh clone,
          shallow history, or a repository where all tracked files have the
          same recent commit pattern. Try re-indexing once more history is
          available.
        </p>
      </div>
    );
  }

  return (
    <div className={cn("w-full space-y-2", className)}>
      <div className="flex items-center justify-between text-[11px] text-[var(--color-text-tertiary)]">
        <span>
          {points.length} files plotted
        </span>
        <span
          className={cn(
            "inline-flex items-center gap-1 rounded px-1.5 py-0.5 tabular-nums",
            dangerCount > 0
              ? "bg-red-500/15 text-red-400"
              : "bg-green-500/15 text-green-400",
          )}
        >
          <span className="font-medium">{dangerCount}</span>
          in danger zone
        </span>
      </div>
      <svg
        viewBox={`0 0 ${width} ${height}`}
        preserveAspectRatio="xMidYMid meet"
        className="h-auto w-full"
        role="img"
        aria-label="Churn vs bus factor scatter plot"
      >
        {/* Quadrant tint — top-right is the danger zone */}
        <rect
          x={padding.left + innerW * 0.5}
          y={padding.top}
          width={innerW * 0.5}
          height={innerH * 0.5}
          fill="rgba(244, 63, 94, 0.06)"
        />

        {/* Axes */}
        <line
          x1={padding.left}
          y1={padding.top + innerH}
          x2={padding.left + innerW}
          y2={padding.top + innerH}
          stroke="var(--color-border-default)"
        />
        <line
          x1={padding.left}
          y1={padding.top}
          x2={padding.left}
          y2={padding.top + innerH}
          stroke="var(--color-border-default)"
        />

        {/* X labels */}
        {[0, 50, 100].map((tick) => (
          <text
            key={`x-${tick}`}
            x={padding.left + (tick / 100) * innerW}
            y={padding.top + innerH + 16}
            textAnchor="middle"
            fontSize="10"
            fill="var(--color-text-tertiary)"
          >
            {tick}
          </text>
        ))}

        {/* Y labels */}
        {[
          { y: 100, label: "1" },
          { y: 50, label: "4" },
          { y: 0, label: "8+" },
        ].map((t) => (
          <text
            key={`y-${t.label}`}
            x={padding.left - 8}
            y={padding.top + innerH - (t.y / 100) * innerH + 3}
            textAnchor="end"
            fontSize="10"
            fill="var(--color-text-tertiary)"
          >
            {t.label}
          </text>
        ))}

        <text
          x={padding.left + innerW / 2}
          y={height - 4}
          textAnchor="middle"
          fontSize="10"
          fill="var(--color-text-tertiary)"
        >
          Churn percentile →
        </text>
        <text
          x={-padding.top - innerH / 2}
          y={12}
          transform={`rotate(-90)`}
          textAnchor="middle"
          fontSize="10"
          fill="var(--color-text-tertiary)"
        >
          ← Bus factor (1 = fragile)
        </text>

        {/* Points */}
        {points.map((p) => {
          const cx = padding.left + (p._x / 100) * innerW;
          const cy = padding.top + innerH - (p._y / 100) * innerH;
          const fill = p._danger
            ? "rgba(244,63,94,0.7)"
            : "rgba(245,158,11,0.45)";
          return (
            <g key={p.file_path}>
              <circle
                cx={cx}
                cy={cy}
                r={p._r}
                fill={fill}
                stroke={p._danger ? "rgba(244,63,94,0.9)" : "rgba(0,0,0,0.2)"}
                strokeWidth={p._danger ? 1 : 0.5}
                onClick={onSelect ? () => onSelect(p.file_path) : undefined}
                style={{ cursor: onSelect ? "pointer" : "default" }}
              >
                <title>
                  {p.file_path}
                  {"\n"}churn: {Math.round(p._x)}th, bus factor: {p.bus_factor}
                  {", "}commits 90d: {p.commit_count_90d}
                  {p.primary_owner ? `\nowner: ${p.primary_owner}` : ""}
                </title>
              </circle>
            </g>
          );
        })}

        {/* The danger-zone files are listed in the legend below — drawing
            them inline collides whenever multiple points cluster in the
            same quadrant. */}
      </svg>
      {dangerPoints.length > 0 && (
        <ul className="space-y-0.5 text-[11px]">
          {dangerPoints.map((p) => (
            <li
              key={`legend-${p.file_path}`}
              className="flex items-center justify-between gap-2"
            >
              <button
                type="button"
                onClick={onSelect ? () => onSelect(p.file_path) : undefined}
                disabled={!onSelect}
                className={cn(
                  "truncate text-left font-mono text-[var(--color-text-secondary)]",
                  onSelect && "hover:text-[var(--color-accent-primary)] cursor-pointer",
                )}
                title={p.file_path}
              >
                {p.file_path}
              </button>
              <span className="shrink-0 tabular-nums text-[var(--color-text-tertiary)]">
                bus {p.bus_factor} · churn {Math.round(p._x)}th
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
