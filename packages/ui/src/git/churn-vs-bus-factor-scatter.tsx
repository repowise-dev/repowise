"use client";

import * as React from "react";
import { cn } from "../lib/cn";

export interface ScatterHotspot {
  file_path: string;
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
  height = 220,
}: ChurnVsBusFactorScatterProps) {
  const padding = { top: 12, right: 12, bottom: 28, left: 36 };
  const width = 480;
  const innerW = width - padding.left - padding.right;
  const innerH = height - padding.top - padding.bottom;

  const points = React.useMemo(() => {
    return hotspots.map((h) => {
      const x = Math.max(0, Math.min(100, h.churn_percentile));
      // bus_factor of 1 → top of chart (riskiest); cap at 8 for visual scale.
      const busClamped = Math.max(1, Math.min(8, h.bus_factor || 1));
      const y = ((8 - busClamped) / 7) * 100;
      // Bubble size by commit_count_90d (sqrt for area-ish scaling).
      const r = 4 + Math.min(14, Math.sqrt(h.commit_count_90d ?? 0));
      return { ...h, _x: x, _y: y, _r: r };
    });
  }, [hotspots]);

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

  return (
    <div className={cn("w-full", className)}>
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
          const fill =
            p._x >= 50 && p._y >= 50
              ? "rgba(244,63,94,0.6)"
              : "rgba(245,158,11,0.5)";
          return (
            <g key={p.file_path}>
              <circle
                cx={cx}
                cy={cy}
                r={p._r}
                fill={fill}
                stroke="rgba(0,0,0,0.2)"
                strokeWidth={0.5}
                onClick={onSelect ? () => onSelect(p.file_path) : undefined}
                style={{ cursor: onSelect ? "pointer" : "default" }}
              >
                <title>
                  {p.file_path}
                  {"\n"}churn: {Math.round(p.churn_percentile)}th, bus factor: {p.bus_factor}
                  {", "}commits 90d: {p.commit_count_90d}
                </title>
              </circle>
            </g>
          );
        })}
      </svg>
    </div>
  );
}
