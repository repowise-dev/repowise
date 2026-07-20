"use client";

import * as React from "react";
import type { CommitStats, ReviewPriority } from "@repowise-dev/types/git";
import { cn } from "../lib/cn";

/**
 * The distribution behind the review-priority terciles: how this repo's raw
 * change-risk scores are actually spread, with the low/typical and
 * typical/elevated cut lines drawn on top.
 *
 * Binned on the **raw 0-10 score**, never the percentile — percentile ranks
 * are uniform by construction, so a histogram of them is a flat block that
 * says nothing. The raw axis is where the shape lives.
 *
 * Colours match {@link PriorityBadge}: calm below the cuts, warning above, so
 * a bar and a row pill for the same commit never disagree.
 */

const BAND_FILL: Record<ReviewPriority, string> = {
  low: "color-mix(in srgb, var(--color-success) 55%, transparent)",
  moderate: "color-mix(in srgb, var(--color-text-tertiary) 45%, transparent)",
  high: "color-mix(in srgb, var(--color-warning) 70%, transparent)",
};

const BAND_LABEL: Record<ReviewPriority, string> = {
  low: "Below typical",
  moderate: "Typical",
  high: "Elevated",
};

const BAND_ORDER: ReviewPriority[] = ["low", "moderate", "high"];

function bandFor(
  binStart: number,
  moderateCut: number | null,
  highCut: number | null,
): ReviewPriority {
  if (highCut != null && binStart >= highCut) return "high";
  if (moderateCut != null && binStart >= moderateCut) return "moderate";
  return "low";
}

export function CommitRiskHistogram({
  stats,
  className,
}: {
  stats: CommitStats;
  className?: string;
}) {
  const [hover, setHover] = React.useState<number | null>(null);

  const buckets = stats.risk_histogram ?? [];
  const moderateCut = stats.moderate_cut ?? null;
  const highCut = stats.high_cut ?? null;

  const total = React.useMemo(
    () => buckets.reduce((s, b) => s + b.count, 0),
    [buckets],
  );

  // Trim the empty tail so a repo whose scores top out at 6 doesn't spend
  // 40% of the axis on bins that can never fill.
  const shown = React.useMemo(() => {
    let last = buckets.length - 1;
    while (last > 0 && buckets[last]?.count === 0) last--;
    return buckets.slice(0, last + 1);
  }, [buckets]);

  if (shown.length === 0 || total === 0) return null;

  const padding = { top: 10, right: 10, bottom: 26, left: 30 };
  const width = 480;
  const height = 200;
  const innerW = width - padding.left - padding.right;
  const innerH = height - padding.top - padding.bottom;

  const xMax = shown[shown.length - 1]?.end ?? 10;
  const yMax = Math.max(...shown.map((b) => b.count));
  const barW = innerW / shown.length;
  const xOf = (score: number) => padding.left + (score / xMax) * innerW;

  const hovered = hover != null ? shown[hover] : null;

  return (
    <div className={cn("w-full space-y-2", className)}>
      <div className="relative">
        <svg
          viewBox={`0 0 ${width} ${height}`}
          preserveAspectRatio="xMidYMid meet"
          className="h-auto w-full"
          role="img"
          aria-label="Histogram of commit change-risk scores across the repository"
        >
          {/* Baseline + y axis, both recessive */}
          <line
            x1={padding.left}
            y1={padding.top + innerH}
            x2={padding.left + innerW}
            y2={padding.top + innerH}
            stroke="var(--color-border-default)"
          />

          {/* Two y ticks is enough — the shape matters, the exact counts live
              in the tooltip. */}
          {[0, yMax].map((tick) => (
            <text
              key={`y-${tick}`}
              x={padding.left - 6}
              y={padding.top + innerH - (tick / yMax) * innerH + 3}
              textAnchor="end"
              fontSize="10"
              fill="var(--color-text-tertiary)"
            >
              {tick}
            </text>
          ))}

          {/* Bars — 2px surface gap between neighbours per the mark spec */}
          {shown.map((b, i) => {
            const h = yMax > 0 ? (b.count / yMax) * innerH : 0;
            const band = bandFor(b.start, moderateCut, highCut);
            return (
              <rect
                key={b.start}
                x={padding.left + i * barW + 1}
                y={padding.top + innerH - h}
                width={Math.max(1, barW - 2)}
                height={h}
                rx={2}
                fill={BAND_FILL[band]}
                stroke={hover === i ? "var(--color-text-secondary)" : "none"}
                strokeWidth={1}
                onMouseEnter={() => setHover(i)}
                onMouseLeave={() => setHover(null)}
              />
            );
          })}

          {/* Hit targets wider than the marks, so thin bars stay hoverable */}
          {shown.map((b, i) => (
            <rect
              key={`hit-${b.start}`}
              x={padding.left + i * barW}
              y={padding.top}
              width={barW}
              height={innerH}
              fill="transparent"
              onMouseEnter={() => setHover(i)}
              onMouseLeave={() => setHover(null)}
            />
          ))}

          {/* The tercile cuts — the whole point of drawing this. On a skewed
              repo both cuts crowd the right edge, so the labels stack on
              separate lines and flip to the inside when they'd overflow. */}
          {[
            { at: moderateCut, label: "typical", row: 0 },
            { at: highCut, label: "elevated", row: 1 },
          ].map(({ at, label, row }) => {
            if (at == null || at <= 0 || at >= xMax) return null;
            const x = xOf(at);
            const flip = x > padding.left + innerW * 0.78;
            return (
              <g key={label}>
                <line
                  x1={x}
                  y1={padding.top}
                  x2={x}
                  y2={padding.top + innerH}
                  stroke="var(--color-text-tertiary)"
                  strokeDasharray="4 3"
                />
                <text
                  x={flip ? x - 4 : x + 4}
                  y={padding.top + 9 + row * 11}
                  textAnchor={flip ? "end" : "start"}
                  fontSize="9"
                  fill="var(--color-text-tertiary)"
                >
                  {flip ? `↑ ${label}` : `${label} ↑`}
                </text>
              </g>
            );
          })}

          {/* X labels */}
          {[0, xMax / 2, xMax].map((tick) => (
            <text
              key={`x-${tick}`}
              x={xOf(tick)}
              y={padding.top + innerH + 14}
              textAnchor="middle"
              fontSize="10"
              fill="var(--color-text-tertiary)"
            >
              {tick.toFixed(1)}
            </text>
          ))}
          <text
            x={padding.left + innerW / 2}
            y={height - 2}
            textAnchor="middle"
            fontSize="10"
            fill="var(--color-text-tertiary)"
          >
            Change-risk score →
          </text>
        </svg>

        {hovered && (
          <div className="pointer-events-none absolute left-1/2 top-0 -translate-x-1/2 rounded-lg border border-[var(--color-border-default)] bg-[var(--color-bg-overlay)] px-2.5 py-1.5 text-xs shadow-md">
            <span className="tabular-nums text-[var(--color-text-primary)]">
              {hovered.start.toFixed(1)}–{hovered.end.toFixed(1)}
            </span>
            <span className="ml-2 tabular-nums text-[var(--color-text-secondary)]">
              {hovered.count} commit{hovered.count === 1 ? "" : "s"} (
              {Math.round((hovered.count / total) * 100)}%)
            </span>
          </div>
        )}
      </div>

      {/* Identity only, no counts: a bin that straddles a cut is drawn in one
          band but its commits split across two, so bin-summed totals would
          contradict the high-priority stat card above. */}
      <div className="flex flex-wrap gap-x-4 gap-y-1.5">
        {BAND_ORDER.map((band) => (
          <div key={band} className="flex items-center gap-1.5 text-xs">
            <span
              className="h-2.5 w-2.5 shrink-0 rounded-[3px]"
              style={{ background: BAND_FILL[band] }}
            />
            <span className="text-[var(--color-text-secondary)]">
              {BAND_LABEL[band]}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}
