"use client";

import { TrendingDown } from "lucide-react";
import type { FileHealthTrend } from "@repowise-dev/types/health";
import { formatDate } from "../lib/format";
import { deltaColor, formatDelta, scoreTextColor } from "./tokens";

export interface FileTrendChartProps {
  trend: FileHealthTrend | null | undefined;
  height?: number;
  /** Hide the bordered card chrome (e.g. when embedding inside another card). */
  bare?: boolean;
}

/** The score floor. Below it the visible score stops moving; see `plotted`. */
const SCORE_FLOOR = 1;

/**
 * The value actually drawn for a point: the score with the floor undone where
 * the server recorded how deep the file is, and the plain score otherwise.
 *
 * Two files 12.9 and 9.1 points deep both print 1.0, so a floored file's line
 * is flat however much of the work gets done — which is the opposite of the
 * feedback someone who just did that work needs. Plotting the unclamped value
 * is identical for every file the floor never touches, so there is one code
 * path rather than a mode.
 */
const plotted = (p: FileHealthTrend["points"][number]) => p.unclamped_score ?? p.score;

/**
 * A single file's score trajectory over the snapshot history — the per-file
 * counterpart to the repo-level `TrendChart`. Purpose-built for one series
 * (rather than overloading the 3-KPI chart): a 0-10 Y axis that extends
 * downward only when a file has sunk below the floor, a delta chip, and a
 * declining flag. Silent ("no history yet") when the file has fewer than two
 * snapshots, matching the silent-on-thin-history contract.
 */
export function FileTrendChart({ trend, height = 140, bare = false }: FileTrendChartProps) {
  const points = trend?.points ?? [];
  const belowFloor = points.some((p) => plotted(p) < SCORE_FLOOR);
  // Equal to `delta` unless the floor is in play, so it needs no branch.
  const delta = trend?.unclamped_delta ?? trend?.delta;

  const body =
    points.length < 2 ? (
      <div className="rounded-md border border-dashed border-[var(--color-border-default)] bg-[var(--color-bg-surface)] p-4 text-center text-xs text-[var(--color-text-tertiary)]">
        No score history yet. Trends appear once this file has been scored in at
        least two <code>repowise</code> runs.
      </div>
    ) : (
      <>
        <Chart points={points} height={height} />
        {belowFloor && (
          <p className="text-[11px] leading-snug text-[var(--color-text-tertiary)]">
            This file scores below the {SCORE_FLOOR.toFixed(1)} floor, so its
            displayed score cannot move. The line shows the score before the
            floor is applied, which is what improvements here shift first.
          </p>
        )}
      </>
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
            {delta != null && delta !== 0 && (
              <span className={`text-xs font-semibold tabular-nums ${deltaColor(delta)}`}>
                {formatDelta(delta)} vs. previous
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

  const values = points.map(plotted);
  // 0-10 normally. A file below the floor is plotted on its real depth, which
  // can be negative, so the domain drops to the next whole point beneath it —
  // never above 0, so the usual chart is pixel-identical to before.
  const yMin = Math.min(0, Math.floor(Math.min(...values)));
  const yMax = 10;
  const ticks = yMin < 0 ? [yMin, 0, 5, 10] : [0, 5, 10];
  // Gated on the same condition as the explanatory caption above. Keying the
  // marker off `yMin < 0` instead left a file 9 to 10 points deep — unclamped
  // inside [0, 1) — captioned as below the floor with no floor drawn.
  const showFloor = Math.min(...values) < SCORE_FLOOR;

  const xScale = (i: number) =>
    points.length === 1 ? padL + plotW / 2 : padL + (i / (points.length - 1)) * plotW;
  const yScale = (v: number) => padT + ((yMax - v) / (yMax - yMin)) * plotH;

  const coords = values.map((v, i) => [xScale(i), yScale(v)] as const);
  const line = coords.map(([x, y], i) => (i === 0 ? `M${x},${y}` : `L${x},${y}`)).join(" ");

  const last = points[points.length - 1]!;
  const first = points[0]!;
  // Banded on the score the product shows elsewhere, not on the plotted depth,
  // so the end dot's colour matches the file's stated score.
  const endColor = scoreTextColor(last.score);

  const fmtDate = (iso: string | null) => (iso ? formatDate(iso) : "");

  return (
    <div className="rounded-md border border-[var(--color-border-default)] bg-[var(--color-bg-surface)] p-2">
      <svg
        viewBox={`0 0 ${W} ${H}`}
        width="100%"
        height={H}
        role="img"
        aria-label="File health score over time"
      >
        {ticks.map((v) => (
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
        {/* The floor, drawn only when the line crosses it — otherwise it would
            be a second axis rule at the bottom of every ordinary chart. */}
        {showFloor && (
          <>
            <line
              x1={padL}
              x2={W - padR}
              y1={yScale(SCORE_FLOOR)}
              y2={yScale(SCORE_FLOOR)}
              stroke="var(--color-error)"
              strokeOpacity={0.45}
              strokeDasharray="3 3"
            />
            <text
              x={W - padR}
              y={yScale(SCORE_FLOOR) - 3}
              fontSize={9}
              textAnchor="end"
              fill="var(--color-error)"
              opacity={0.7}
            >
              score floor
            </text>
          </>
        )}
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
