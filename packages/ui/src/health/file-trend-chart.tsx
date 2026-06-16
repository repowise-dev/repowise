"use client";

import { TrendingDown } from "lucide-react";
import type { FileHealthTrend } from "@repowise-dev/types/health";
import { deltaColor, formatDelta, scoreTextColor } from "./tokens";

export interface FileTrendChartProps {
  trend: FileHealthTrend | null | undefined;
  height?: number;
  /** Hide the bordered card chrome (e.g. when embedding inside another card). */
  bare?: boolean;
}

/**
 * A single file's score trajectory over the snapshot history — the per-file
 * counterpart to the repo-level `TrendChart`. Purpose-built for one 0-10
 * series (rather than overloading the 3-KPI chart): a fixed 0-10 Y axis, a
 * delta chip, and a declining flag. Silent ("no history yet") when the file
 * has fewer than two snapshots, matching the silent-on-thin-history contract.
 */
export function FileTrendChart({ trend, height = 140, bare = false }: FileTrendChartProps) {
  const points = trend?.points ?? [];

  const body =
    points.length < 2 ? (
      <div className="rounded-md border border-dashed border-[var(--color-border-default)] bg-[var(--color-bg-surface)] p-4 text-center text-xs text-[var(--color-text-tertiary)]">
        No score history yet. Trends appear once this file has been scored in at
        least two <code>repowise</code> runs.
      </div>
    ) : (
      <Chart points={points} height={height} />
    );

  if (bare) return body;

  return (
    <section className="space-y-2">
      <div className="flex items-center justify-between gap-3">
        <h3 className="text-xs font-medium uppercase tracking-wider text-[var(--color-text-tertiary)]">
          Score over time
        </h3>
        {points.length >= 2 && (
          <div className="flex items-center gap-2">
            {trend?.declining && (
              <span className="inline-flex items-center gap-1 rounded bg-[var(--color-error)]/15 px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wider text-[var(--color-error)]">
                <TrendingDown className="h-3 w-3" />
                Declining
              </span>
            )}
            {trend?.delta != null && trend.delta !== 0 && (
              <span className={`text-xs font-semibold tabular-nums ${deltaColor(trend.delta)}`}>
                {formatDelta(trend.delta)} vs. previous
              </span>
            )}
          </div>
        )}
      </div>
      {body}
    </section>
  );
}

function Chart({
  points,
  height,
}: {
  points: FileHealthTrend["points"];
  height: number;
}) {
  const W = 720;
  const H = height;
  const padL = 28;
  const padR = 12;
  const padT = 10;
  const padB = 22;
  const plotW = W - padL - padR;
  const plotH = H - padT - padB;

  const xScale = (i: number) =>
    points.length === 1 ? padL + plotW / 2 : padL + (i / (points.length - 1)) * plotW;
  const yScale = (v: number) => padT + ((10 - v) / 10) * plotH;

  const coords = points.map((p, i) => [xScale(i), yScale(p.score)] as const);
  const line = coords.map(([x, y], i) => (i === 0 ? `M${x},${y}` : `L${x},${y}`)).join(" ");

  const last = points[points.length - 1]!;
  const first = points[0]!;
  const endColor = scoreTextColor(last.score);

  const fmtDate = (iso: string | null) => (iso ? new Date(iso).toLocaleDateString() : "");

  return (
    <div className="rounded-md border border-[var(--color-border-default)] bg-[var(--color-bg-surface)] p-2">
      <svg
        viewBox={`0 0 ${W} ${H}`}
        width="100%"
        height={H}
        role="img"
        aria-label="File health score over time"
      >
        {[0, 5, 10].map((v) => (
          <g key={v}>
            <line
              x1={padL}
              x2={W - padR}
              y1={yScale(v)}
              y2={yScale(v)}
              stroke="currentColor"
              strokeOpacity={0.08}
            />
            <text
              x={padL - 5}
              y={yScale(v) + 3}
              fontSize={9}
              textAnchor="end"
              fill="currentColor"
              opacity={0.5}
            >
              {v}
            </text>
          </g>
        ))}
        <path d={line} stroke="var(--color-accent-primary)" strokeWidth={1.8} fill="none" />
        {coords.map(([x, y], i) => (
          <circle key={i} cx={x} cy={y} r={2.2} fill="var(--color-accent-primary)" />
        ))}
        {/* Emphasize the latest point, colored by its current band. */}
        <circle
          cx={coords[coords.length - 1]![0]}
          cy={coords[coords.length - 1]![1]}
          r={3.4}
          className={endColor}
          fill="currentColor"
        />
        <text x={padL} y={H - 6} fontSize={9} fill="currentColor" opacity={0.5}>
          {fmtDate(first.taken_at)}
        </text>
        <text
          x={W - padR}
          y={H - 6}
          fontSize={9}
          textAnchor="end"
          fill="currentColor"
          opacity={0.5}
        >
          {fmtDate(last.taken_at)}
        </text>
      </svg>
    </div>
  );
}
