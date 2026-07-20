"use client";

import * as React from "react";
import type { Commit, ReviewPriority } from "@repowise-dev/types/git";
import { cn } from "../lib/cn";

/**
 * What a risky commit actually looks like in this repo: every commit is a dot
 * at (size, diffusion), coloured by its repo-relative review priority. The
 * top-right corner is the shape the change-risk model penalises most — a large
 * change scattered across many files — so the model explains itself without
 * anyone opening the detail sheet.
 *
 * Size is drawn on a log axis because commit sizes span orders of magnitude; a
 * linear axis pins every ordinary commit to the left edge.
 */

const PRIORITY_FILL: Record<ReviewPriority, string> = {
  low: "color-mix(in srgb, var(--color-success) 55%, transparent)",
  moderate: "color-mix(in srgb, var(--color-text-tertiary) 45%, transparent)",
  high: "color-mix(in srgb, var(--color-warning) 75%, transparent)",
};

const PRIORITY_LABEL: Record<ReviewPriority, string> = {
  low: "Below typical",
  moderate: "Typical",
  high: "Elevated",
};

const PRIORITY_ORDER: ReviewPriority[] = ["low", "moderate", "high"];

export function CommitRiskScatter({
  commits,
  onSelect,
  className,
}: {
  commits: Commit[];
  onSelect?: (sha: string) => void;
  className?: string;
}) {
  const [hover, setHover] = React.useState<string | null>(null);

  const padding = { top: 12, right: 12, bottom: 30, left: 34 };
  const width = 480;
  const height = 200;
  const innerW = width - padding.left - padding.right;
  const innerH = height - padding.top - padding.bottom;

  const { points, xMaxLog, yMax } = React.useMemo(() => {
    const sized = commits.map((c) => ({
      commit: c,
      churn: (c.lines_added || 0) + (c.lines_deleted || 0),
      entropy: c.entropy || 0,
    }));
    const xMaxLogRaw = Math.max(
      1,
      ...sized.map((s) => Math.log10(1 + s.churn)),
    );
    const yMaxRaw = Math.max(1, ...sized.map((s) => s.entropy));
    return {
      points: sized.map((s) => ({
        ...s,
        // Bigger dot = more files touched, sqrt so area (not radius) scales.
        r: 3 + Math.min(9, Math.sqrt(s.commit.files_changed || 0)),
        _x: Math.log10(1 + s.churn) / xMaxLogRaw,
        _y: s.entropy / yMaxRaw,
      })),
      xMaxLog: xMaxLogRaw,
      yMax: yMaxRaw,
    };
  }, [commits]);

  const counts = React.useMemo(() => {
    const out: Record<ReviewPriority, number> = { low: 0, moderate: 0, high: 0 };
    for (const p of points) out[p.commit.review_priority] += 1;
    return out;
  }, [points]);

  if (points.length === 0) return null;

  const hovered = hover ? points.find((p) => p.commit.sha === hover) : null;

  // Draw elevated commits last so they sit above the calm ones where dots overlap.
  const ordered = [...points].sort(
    (a, b) =>
      PRIORITY_ORDER.indexOf(a.commit.review_priority) -
      PRIORITY_ORDER.indexOf(b.commit.review_priority),
  );

  return (
    <div className={cn("w-full space-y-2", className)}>
      <div className="relative">
        <svg
          viewBox={`0 0 ${width} ${height}`}
          preserveAspectRatio="xMidYMid meet"
          className="h-auto w-full"
          role="img"
          aria-label="Commit size versus change diffusion, coloured by review priority"
        >
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

          {/* X ticks at decade boundaries — the log axis is only honest if it
              is labelled as one. */}
          {Array.from({ length: Math.floor(xMaxLog) + 1 }, (_, d) => d).map((d) => (
            <text
              key={`x-${d}`}
              x={padding.left + (d / xMaxLog) * innerW}
              y={padding.top + innerH + 14}
              textAnchor="middle"
              fontSize="10"
              fill="var(--color-text-tertiary)"
            >
              {Math.pow(10, d).toLocaleString()}
            </text>
          ))}
          <text
            x={padding.left + innerW / 2}
            y={height - 2}
            textAnchor="middle"
            fontSize="10"
            fill="var(--color-text-tertiary)"
          >
            Lines changed (log) →
          </text>

          {/* Y ticks */}
          {[0, yMax].map((t) => (
            <text
              key={`y-${t}`}
              x={padding.left - 6}
              y={padding.top + innerH - (t / yMax) * innerH + 3}
              textAnchor="end"
              fontSize="10"
              fill="var(--color-text-tertiary)"
            >
              {t.toFixed(1)}
            </text>
          ))}
          <text
            x={-padding.top - innerH / 2}
            y={11}
            transform="rotate(-90)"
            textAnchor="middle"
            fontSize="10"
            fill="var(--color-text-tertiary)"
          >
            Diffusion →
          </text>

          {ordered.map((p) => {
            const cx = padding.left + p._x * innerW;
            const cy = padding.top + innerH - p._y * innerH;
            const isHover = hover === p.commit.sha;
            return (
              <circle
                key={p.commit.sha}
                cx={cx}
                cy={cy}
                r={isHover ? p.r + 2 : p.r}
                fill={PRIORITY_FILL[p.commit.review_priority]}
                // 2px surface ring so overlapping dots stay countable.
                stroke={isHover ? "var(--color-text-primary)" : "var(--color-bg-surface)"}
                strokeWidth={isHover ? 1.5 : 2}
                onMouseEnter={() => setHover(p.commit.sha)}
                onMouseLeave={() => setHover(null)}
                onClick={onSelect ? () => onSelect(p.commit.sha) : undefined}
                style={{ cursor: onSelect ? "pointer" : "default" }}
              />
            );
          })}
        </svg>

        {hovered && (
          <div className="pointer-events-none absolute left-1/2 top-0 max-w-[280px] -translate-x-1/2 rounded-lg border border-[var(--color-border-default)] bg-[var(--color-bg-overlay)] px-2.5 py-1.5 text-xs shadow-md">
            <p className="truncate font-medium text-[var(--color-text-primary)]">
              {hovered.commit.subject}
            </p>
            <p className="tabular-nums text-[var(--color-text-secondary)]">
              {hovered.churn.toLocaleString()} lines ·{" "}
              {hovered.commit.files_changed} file
              {hovered.commit.files_changed === 1 ? "" : "s"} · diffusion{" "}
              {hovered.entropy.toFixed(2)}
            </p>
            <p className="text-[var(--color-text-tertiary)]">
              {PRIORITY_LABEL[hovered.commit.review_priority]} ·{" "}
              {hovered.commit.short_sha}
            </p>
          </div>
        )}
      </div>

      <div className="flex flex-wrap gap-x-4 gap-y-1.5">
        {PRIORITY_ORDER.map((p) => (
          <div key={p} className="flex items-center gap-1.5 text-xs">
            <span
              className="h-2.5 w-2.5 shrink-0 rounded-full"
              style={{ background: PRIORITY_FILL[p] }}
            />
            <span className="text-[var(--color-text-secondary)]">
              {PRIORITY_LABEL[p]}
            </span>
            <span className="tabular-nums text-[var(--color-text-tertiary)]">
              {counts[p]}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}
