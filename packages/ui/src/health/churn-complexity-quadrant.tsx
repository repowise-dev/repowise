"use client";

import { useMemo, useState } from "react";
// Value imports must come from the package root, not the `/health` subpath:
// the vite/rollup base alias clobbers subpath value resolution. Type-only
// subpath imports are fine (erased before resolution).
import { bandForScore } from "@repowise-dev/types";
import type { ChurnComplexityPoint, HealthBand } from "@repowise-dev/types/health";

export interface ChurnComplexityQuadrantProps {
  points: ChurnComplexityPoint[];
  onSelect?: (point: ChurnComplexityPoint) => void;
  height?: number;
}

/* SVG fill class per canonical health band (the 3-bucket currency). Literal
 * strings so Tailwind's static scanner keeps them. */
const BAND_FILL: Record<HealthBand, string> = {
  alert: "fill-[var(--color-error)]",
  warning: "fill-[var(--color-caution)]",
  healthy: "fill-[var(--color-success)]",
};

/** Median of a numeric list (the data-driven quadrant split). */
function median(values: number[]): number {
  if (values.length === 0) return 0;
  const sorted = [...values].sort((a, b) => a - b);
  const mid = Math.floor(sorted.length / 2);
  return sorted.length % 2 === 0 ? (sorted[mid - 1]! + sorted[mid]!) / 2 : sorted[mid]!;
}

/**
 * Churn x complexity quadrant -- the "hotspot anatomy" view. X = 90-day commit
 * count (how often the file changes), Y = max cyclomatic complexity (how
 * tangled it is). Dot size encodes NLOC; dot color is the health band. The
 * dashed guides sit at the repo's median churn and median complexity, so the
 * top-right tinted corner reads "busier AND more complex than a typical file
 * here" -- the danger zone where defects concentrate and refactoring pays off.
 *
 * Inline SVG (no chart dep) so it stays cheap and reusable, matching the other
 * health scatters.
 */
export function ChurnComplexityQuadrant({
  points,
  onSelect,
  height = 320,
}: ChurnComplexityQuadrantProps) {
  const [hovered, setHovered] = useState<ChurnComplexityPoint | null>(null);
  const data = useMemo(
    () => points.filter((p) => p.commit_count_90d > 0 && Number.isFinite(p.max_ccn)),
    [points],
  );

  const layout = useMemo(() => {
    const maxCommits = Math.max(...data.map((d) => d.commit_count_90d), 1);
    const maxCcn = Math.max(...data.map((d) => d.max_ccn), 1);
    const maxNloc = Math.max(...data.map((d) => d.nloc), 1);
    return {
      maxCommits,
      maxCcn,
      maxNloc,
      medCommits: median(data.map((d) => d.commit_count_90d)),
      medCcn: median(data.map((d) => d.max_ccn)),
    };
  }, [data]);

  if (data.length === 0) {
    return (
      <div
        className="rounded-lg border border-dashed border-[var(--color-border-default)] bg-[var(--color-bg-surface)] p-6 text-sm text-[var(--color-text-tertiary)] text-center"
        style={{ minHeight: height }}
      >
        No recently-changed files to plot yet. Churn x complexity needs git history.
      </div>
    );
  }

  const W = 640;
  const H = height;
  const padL = 44;
  const padR = 12;
  const padT = 24;
  const padB = 34;
  const plotW = W - padL - padR;
  const plotH = H - padT - padB;

  // 5% headroom so points never sit on the frame.
  const xMax = layout.maxCommits * 1.05;
  const yMax = layout.maxCcn * 1.05;
  const xScale = (commits: number) => padL + (commits / xMax) * plotW;
  const yScale = (ccn: number) => padT + ((yMax - ccn) / yMax) * plotH;
  const r = (nloc: number) => 2 + Math.min(7, Math.sqrt(nloc / layout.maxNloc) * 7);

  const midX = xScale(layout.medCommits);
  const midY = yScale(layout.medCcn);

  return (
    <div className="rounded-lg border border-[var(--color-border-default)] bg-[var(--color-bg-surface)] p-3">
      <div className="mb-2 flex items-baseline gap-2">
        <h3 className="text-sm font-semibold text-[var(--color-text-primary)]">
          Churn x complexity
        </h3>
        <span className="text-xs text-[var(--color-text-tertiary)]">
          {data.length} files · dot size = NLOC · color = health
        </span>
      </div>
      <div className="relative">
        <svg viewBox={`0 0 ${W} ${H}`} width="100%" height={H} role="img" aria-label="Churn vs complexity scatter plot">
          {/* Danger-zone tint — top-right (high churn, high complexity) */}
          <rect x={midX} y={padT} width={W - padR - midX} height={midY - padT} fill="currentColor" className="text-[var(--color-error)]/8" />

          {/* Axes */}
          <line x1={padL} y1={padT} x2={padL} y2={H - padB} stroke="currentColor" strokeOpacity={0.2} />
          <line x1={padL} y1={H - padB} x2={W - padR} y2={H - padB} stroke="currentColor" strokeOpacity={0.2} />
          {/* Median guide lines */}
          <line x1={midX} y1={padT} x2={midX} y2={H - padB} stroke="currentColor" strokeOpacity={0.12} strokeDasharray="3 3" />
          <line x1={padL} y1={midY} x2={W - padR} y2={midY} stroke="currentColor" strokeOpacity={0.12} strokeDasharray="3 3" />

          {/* Axis labels */}
          <text x={W / 2} y={H - 4} fontSize={10} textAnchor="middle" fill="currentColor" opacity={0.6}>
            Commits (90 days) →
          </text>
          <text x={12} y={H / 2} fontSize={10} textAnchor="middle" fill="currentColor" opacity={0.6} transform={`rotate(-90 12 ${H / 2})`}>
            Complexity (max CCN) →
          </text>
          <text x={midX} y={H - padB + 14} fontSize={9} textAnchor="middle" fill="currentColor" opacity={0.4}>
            median
          </text>

          {/* Quadrant captions */}
          <text x={W - padR - 6} y={padT + 13} fontSize={10} textAnchor="end" fill="currentColor" opacity={0.6} className="font-medium">
            Refactor zone
          </text>
          <text x={padL + 6} y={padT + 13} fontSize={10} fill="currentColor" opacity={0.45}>Complex, but stable</text>
          <text x={W - padR - 6} y={H - padB - 6} fontSize={10} textAnchor="end" fill="currentColor" opacity={0.45}>Churns, but simple</text>
          <text x={padL + 6} y={H - padB - 6} fontSize={10} fill="currentColor" opacity={0.4}>Calm &amp; simple</text>

          {/* Points */}
          {data.map((p) => {
            const cx = xScale(p.commit_count_90d);
            const cy = yScale(p.max_ccn);
            const isHovered = hovered?.file_path === p.file_path;
            const fillCls = BAND_FILL[bandForScore(p.score)];
            return (
              <circle
                key={p.file_path}
                cx={cx}
                cy={cy}
                r={r(p.nloc) * (isHovered ? 1.4 : 1)}
                className={`${fillCls} ${onSelect ? "cursor-pointer" : ""}`}
                fillOpacity={0.7}
                stroke={isHovered ? "white" : "none"}
                strokeWidth={1.5}
                onMouseEnter={() => setHovered(p)}
                onMouseLeave={() => setHovered(null)}
                onClick={onSelect ? () => onSelect(p) : undefined}
              >
                <title>{`${p.file_path}\n${p.commit_count_90d} commits/90d (${Math.round(p.churn_percentile)}th pct) · CCN ${p.max_ccn} · NLOC ${p.nloc} · score ${p.score.toFixed(1)}`}</title>
              </circle>
            );
          })}
        </svg>
        {hovered ? (
          <div className="pointer-events-none absolute bottom-2 left-2 rounded-md border border-[var(--color-border-default)] bg-[var(--color-bg-elevated)] px-2 py-1 text-[11px] shadow-md">
            <span className="font-mono text-[var(--color-text-primary)]">{hovered.file_path}</span>
            <span className="ml-2 text-[var(--color-text-tertiary)]">
              {hovered.commit_count_90d} commits · CCN {hovered.max_ccn} · {hovered.score.toFixed(1)}
            </span>
          </div>
        ) : null}
      </div>
    </div>
  );
}
