"use client";

import { useMemo, useState } from "react";
import type { EffortBucket } from "./refactoring-card";

export interface ImpactEffortPoint {
  file_path: string;
  total_impact: number;
  effort_bucket: EffortBucket;
  nloc: number;
  score: number;
}

export interface ImpactEffortQuadrantProps {
  points: ImpactEffortPoint[];
  onSelect?: (point: ImpactEffortPoint) => void;
  height?: number;
}

const EFFORT_X: Record<EffortBucket, number> = { S: 12, M: 38, L: 62, XL: 88 };

export function ImpactEffortQuadrant({
  points,
  onSelect,
  height = 280,
}: ImpactEffortQuadrantProps) {
  const [hovered, setHovered] = useState<ImpactEffortPoint | null>(null);
  const data = useMemo(() => points.filter((p) => p.total_impact > 0), [points]);

  if (data.length === 0) {
    return (
      <div className="rounded-lg border border-dashed border-[var(--color-border-default)] bg-[var(--color-bg-surface)] p-6 text-sm text-[var(--color-text-tertiary)]">
        No refactoring targets to plot yet.
      </div>
    );
  }

  const W = 640;
  const H = height;
  const padL = 40;
  const padR = 12;
  const padT = 20;
  const padB = 36;
  const plotW = W - padL - padR;
  const plotH = H - padT - padB;
  const maxImpact = Math.max(...data.map((d) => d.total_impact), 1);
  const minImpact = Math.min(...data.map((d) => d.total_impact));
  // When every impact is identical (or zero) the (max−impact)/max ratio would
  // collapse every dot onto the top edge and degenerate the midline. Center the
  // dots vertically instead so the plot stays readable.
  const flatImpact = maxImpact === minImpact || maxImpact === 0;

  const xScale = (pct: number) => padL + (pct / 100) * plotW;
  const yScale = (impact: number) =>
    flatImpact ? padT + plotH / 2 : padT + ((maxImpact - impact) / maxImpact) * plotH;

  // Quadrant midpoints — between M (38) and L (62), and at half impact.
  const midX = xScale(50);
  const midY = flatImpact ? padT + plotH / 2 : yScale(maxImpact / 2);

  // Jitter helper so dots within the same effort bucket don't perfectly stack.
  const jitter = (s: string) => {
    let h = 0;
    for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) | 0;
    return ((h % 14) - 7) * 1.4;
  };

  return (
    <div className="rounded-lg border border-[var(--color-border-default)] bg-[var(--color-bg-surface)] p-3">
      <div className="mb-2 flex items-baseline gap-2">
        <h3 className="text-sm font-semibold text-[var(--color-text-primary)]">
          Impact × effort
        </h3>
        <span className="text-xs text-[var(--color-text-tertiary)]">
          {data.length} targets · click a dot to open
        </span>
      </div>
      <div className="relative">
        <svg viewBox={`0 0 ${W} ${H}`} width="100%" height={H} role="img" aria-label="Impact vs effort scatter plot">
          <rect x={padL} y={padT} width={midX - padL} height={midY - padT} fill="currentColor" className="text-[var(--color-success)]/8" />
          <rect x={midX} y={padT} width={W - padR - midX} height={midY - padT} fill="currentColor" className="text-[var(--color-warning)]/5" />
          <rect x={padL} y={midY} width={midX - padL} height={H - padB - midY} fill="currentColor" className="text-[var(--color-caution)]/5" />
          <rect x={midX} y={midY} width={W - padR - midX} height={H - padB - midY} fill="currentColor" className="text-[var(--color-error)]/5" />

          <line x1={padL} y1={padT} x2={padL} y2={H - padB} stroke="currentColor" strokeOpacity={0.2} />
          <line x1={padL} y1={H - padB} x2={W - padR} y2={H - padB} stroke="currentColor" strokeOpacity={0.2} />
          <line x1={midX} y1={padT} x2={midX} y2={H - padB} stroke="currentColor" strokeOpacity={0.1} strokeDasharray="3 3" />
          <line x1={padL} y1={midY} x2={W - padR} y2={midY} stroke="currentColor" strokeOpacity={0.1} strokeDasharray="3 3" />

          {(["S", "M", "L", "XL"] as EffortBucket[]).map((b) => (
            <text key={b} x={xScale(EFFORT_X[b])} y={H - padB + 14} fontSize={10} textAnchor="middle" fill="currentColor" opacity={0.5}>
              {b}
            </text>
          ))}
          <text x={W / 2} y={H - 4} fontSize={10} textAnchor="middle" fill="currentColor" opacity={0.6}>
            Effort (NLOC bucket)
          </text>
          <text x={10} y={H / 2} fontSize={10} textAnchor="middle" fill="currentColor" opacity={0.6} transform={`rotate(-90 10 ${H / 2})`}>
            Total impact
          </text>

          <text x={padL + 6} y={padT + 12} fontSize={10} fill="currentColor" opacity={0.6}>Quick wins</text>
          <text x={W - padR - 6} y={padT + 12} fontSize={10} textAnchor="end" fill="currentColor" opacity={0.6}>Major projects</text>
          <text x={padL + 6} y={H - padB - 6} fontSize={10} fill="currentColor" opacity={0.5}>Minor cleanups</text>
          <text x={W - padR - 6} y={H - padB - 6} fontSize={10} textAnchor="end" fill="currentColor" opacity={0.5}>Time sinks</text>

          {data.map((p) => {
            const baseX = xScale(EFFORT_X[p.effort_bucket]);
            const cx = baseX + jitter(p.file_path);
            const cy = yScale(p.total_impact);
            const isHovered = hovered?.file_path === p.file_path;
            const fillCls =
              p.score < 4
                ? "fill-[var(--color-error)]"
                : p.score < 6
                  ? "fill-[var(--color-warning)]"
                  : p.score < 8
                    ? "fill-[var(--color-caution)]"
                    : "fill-[var(--color-success)]";
            return (
              <circle
                key={p.file_path}
                cx={cx}
                cy={cy}
                r={isHovered ? 6 : 4}
                className={`${fillCls} ${onSelect ? "cursor-pointer" : ""}`}
                fillOpacity={0.75}
                stroke={isHovered ? "white" : "none"}
                strokeWidth={1.5}
                onMouseEnter={() => setHovered(p)}
                onMouseLeave={() => setHovered(null)}
                onClick={onSelect ? () => onSelect(p) : undefined}
              >
                <title>{`${p.file_path}\n−${p.total_impact.toFixed(2)} · ${p.effort_bucket} effort · score ${p.score.toFixed(1)}`}</title>
              </circle>
            );
          })}
        </svg>
        {hovered ? (
          <div className="pointer-events-none absolute bottom-2 left-2 rounded-md border border-[var(--color-border-default)] bg-[var(--color-bg-elevated)] px-2 py-1 text-xs shadow-md">
            <span className="font-mono text-[var(--color-text-primary)]">{hovered.file_path}</span>
            <span className="ml-2 text-[var(--color-text-tertiary)]">
              −{hovered.total_impact.toFixed(2)} · {hovered.effort_bucket} · {hovered.score.toFixed(1)}
            </span>
          </div>
        ) : null}
      </div>
    </div>
  );
}
