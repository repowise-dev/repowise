"use client";

import { useMemo, useState } from "react";

export interface RiskCoveragePoint {
  file_path: string;
  health_score: number;
  line_coverage_pct: number | null;
  nloc: number;
}

export interface RiskCoverageScatterProps {
  points: RiskCoveragePoint[];
  onSelect?: (point: RiskCoveragePoint) => void;
  height?: number;
}

/**
 * Health × Coverage quadrant chart. Y axis = health (0–10, higher is better),
 * X axis = line coverage % (0–100). Each dot is one file; radius encodes NLOC.
 *
 * Quadrant labels:
 *   - Top-right (high health, high cov):  Sweet spot
 *   - Top-left  (high health, low cov):   Risky — fix tests first
 *   - Bot-right (low health,  high cov):  Tested but messy
 *   - Bot-left  (low health,  low cov):   Critical untested hotspot
 */
export function RiskCoverageScatter({
  points,
  onSelect,
  height = 320,
}: RiskCoverageScatterProps) {
  const [hovered, setHovered] = useState<RiskCoveragePoint | null>(null);
  const data = useMemo(
    () => points.filter((p) => p.line_coverage_pct != null && Number.isFinite(p.health_score)),
    [points],
  );

  if (data.length === 0) {
    return (
      <div
        className="rounded-lg border border-dashed border-[var(--color-border-default)] bg-[var(--color-bg-surface)] p-6 text-sm text-[var(--color-text-tertiary)] text-center"
        style={{ minHeight: height }}
      >
        Ingest a coverage report to see the risk × coverage map for every file.
      </div>
    );
  }

  const W = 640;
  const H = height;
  const padL = 40;
  const padR = 12;
  const padT = 24;
  const padB = 30;
  const plotW = W - padL - padR;
  const plotH = H - padT - padB;

  const maxNloc = Math.max(...data.map((d) => d.nloc), 1);

  const xScale = (pct: number) => padL + (pct / 100) * plotW;
  const yScale = (score: number) => padT + ((10 - score) / 10) * plotH;
  const r = (nloc: number) => 2 + Math.min(7, Math.sqrt(nloc / maxNloc) * 7);

  const midX = xScale(60); // 60% coverage threshold
  const midY = yScale(7); // 7.0 health threshold

  return (
    <div className="rounded-lg border border-[var(--color-border-default)] bg-[var(--color-bg-surface)] p-3">
      <div className="mb-2 flex items-baseline gap-2">
        <h3 className="text-sm font-semibold text-[var(--color-text-primary)]">
          Risk × coverage map
        </h3>
        <span className="text-xs text-[var(--color-text-tertiary)]">
          {data.length} files · dot size = NLOC
        </span>
      </div>
      <div className="relative">
        <svg viewBox={`0 0 ${W} ${H}`} width="100%" height={H} role="img" aria-label="Risk vs coverage scatter plot">
          {/* Quadrant tinting */}
          <rect x={padL} y={padT} width={midX - padL} height={midY - padT} fill="currentColor" className="text-[var(--color-warning)]/5" />
          <rect x={midX} y={padT} width={W - padR - midX} height={midY - padT} fill="currentColor" className="text-[var(--color-success)]/5" />
          <rect x={padL} y={midY} width={midX - padL} height={H - padB - midY} fill="currentColor" className="text-[var(--color-error)]/8" />
          <rect x={midX} y={midY} width={W - padR - midX} height={H - padB - midY} fill="currentColor" className="text-[var(--color-caution)]/5" />

          {/* Axes */}
          <line x1={padL} y1={padT} x2={padL} y2={H - padB} stroke="currentColor" strokeOpacity={0.2} />
          <line x1={padL} y1={H - padB} x2={W - padR} y2={H - padB} stroke="currentColor" strokeOpacity={0.2} />
          <line x1={midX} y1={padT} x2={midX} y2={H - padB} stroke="currentColor" strokeOpacity={0.1} strokeDasharray="3 3" />
          <line x1={padL} y1={midY} x2={W - padR} y2={midY} stroke="currentColor" strokeOpacity={0.1} strokeDasharray="3 3" />

          {/* Axis labels */}
          {[0, 25, 50, 75, 100].map((v) => (
            <g key={`x${v}`}>
              <text x={xScale(v)} y={H - padB + 14} fontSize={10} textAnchor="middle" fill="currentColor" opacity={0.5}>
                {v}%
              </text>
            </g>
          ))}
          {[0, 2, 4, 6, 8, 10].map((v) => (
            <g key={`y${v}`}>
              <text x={padL - 6} y={yScale(v) + 3} fontSize={10} textAnchor="end" fill="currentColor" opacity={0.5}>
                {v}
              </text>
            </g>
          ))}
          <text x={W / 2} y={H - 4} fontSize={10} textAnchor="middle" fill="currentColor" opacity={0.6}>
            Line coverage %
          </text>
          <text x={10} y={H / 2} fontSize={10} textAnchor="middle" fill="currentColor" opacity={0.6} transform={`rotate(-90 10 ${H / 2})`}>
            Health score
          </text>

          {/* Quadrant captions */}
          <text x={padL + 6} y={padT + 14} fontSize={10} fill="currentColor" opacity={0.5}>Risky — needs tests</text>
          <text x={W - padR - 6} y={padT + 14} fontSize={10} textAnchor="end" fill="currentColor" opacity={0.5}>Sweet spot</text>
          <text x={padL + 6} y={H - padB - 6} fontSize={10} fill="currentColor" opacity={0.5}>Critical hotspot</text>
          <text x={W - padR - 6} y={H - padB - 6} fontSize={10} textAnchor="end" fill="currentColor" opacity={0.5}>Tested but messy</text>

          {/* Points */}
          {data.map((p) => {
            const cx = xScale(p.line_coverage_pct ?? 0);
            const cy = yScale(p.health_score);
            const isHovered = hovered?.file_path === p.file_path;
            const fillCls =
              p.health_score < 4
                ? "fill-[var(--color-error)]"
                : p.health_score < 6
                  ? "fill-[var(--color-warning)]"
                  : p.health_score < 8
                    ? "fill-[var(--color-caution)]"
                    : "fill-[var(--color-success)]";
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
                <title>{`${p.file_path}\nHealth ${p.health_score.toFixed(1)} · Cov ${p.line_coverage_pct?.toFixed(0)}% · NLOC ${p.nloc}`}</title>
              </circle>
            );
          })}
        </svg>
        {hovered ? (
          <div className="pointer-events-none absolute bottom-2 left-2 rounded-md border border-[var(--color-border-default)] bg-[var(--color-bg-elevated)] px-2 py-1 text-xs shadow-md">
            <span className="font-mono text-[var(--color-text-primary)]">{hovered.file_path}</span>
            <span className="ml-2 text-[var(--color-text-tertiary)]">
              {hovered.health_score.toFixed(1)} · {hovered.line_coverage_pct?.toFixed(0)}% · {hovered.nloc} NLOC
            </span>
          </div>
        ) : null}
      </div>
    </div>
  );
}
