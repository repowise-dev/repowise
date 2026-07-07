"use client";

import { useMemo, useState } from "react";
import { typeAccent, typeMeta } from "./meta";
import type { EffortBucket, RefactoringPlan } from "./types";

export interface RefactoringQuadrantProps {
  plans: RefactoringPlan[];
  onSelect?: (plan: RefactoringPlan) => void;
  height?: number;
}

const EFFORT_X: Record<EffortBucket, number> = { S: 13, M: 38, L: 63, XL: 87 };

// Plot at most this many dots (the highest-ranked) so hover stays smooth on
// repos that surface hundreds of plans. The list below the quadrant still
// paginates through every plan.
const MAX_DOTS = 120;

/**
 * Priority × effort scatter — the surface's centerpiece. Y is the unified rank
 * score (so graph-native plans with zero recovered health still place by
 * centrality + blast), X is the effort bucket. Dots are colored by refactoring
 * type. The top-left is the sweet spot: high priority, low effort.
 */
export function RefactoringQuadrant({ plans, onSelect, height = 400 }: RefactoringQuadrantProps) {
  const [hovered, setHovered] = useState<RefactoringPlan | null>(null);
  const scored = useMemo(() => plans.filter((p) => p.rank_score > 0), [plans]);
  // The dot budget is limited, and one high-volume type (Extract Helper / clone
  // dedup often numbers in the hundreds) must not consume it all and hide the
  // rest. Seat every other type first, in rank order, then let Extract Helper
  // fill whatever capacity remains — so Split File / Move Method / Break Cycle /
  // Extract Class always get plotted.
  const data = useMemo(() => {
    const primary = scored.filter((p) => p.refactoring_type !== "extract_helper");
    const helpers = scored.filter((p) => p.refactoring_type === "extract_helper");
    return [...primary, ...helpers].slice(0, MAX_DOTS);
  }, [scored]);
  const capped = scored.length > MAX_DOTS;

  if (data.length === 0) {
    return (
      <div className="flex items-center justify-center rounded-2xl border border-dashed border-[var(--color-border-default)] bg-[var(--color-bg-surface)] p-10 text-sm text-[var(--color-text-tertiary)]" style={{ minHeight: height }}>
        No plans to plot yet.
      </div>
    );
  }

  const W = 1100;
  const H = height;
  const padL = 48;
  const padR = 48;
  const padT = 34;
  const padB = 48;
  const plotW = W - padL - padR;
  const plotH = H - padT - padB;
  const maxScore = Math.max(...data.map((d) => d.rank_score), 1);
  const minScore = Math.min(...data.map((d) => d.rank_score));
  const flat = maxScore === minScore;

  const xScale = (pct: number) => padL + (pct / 100) * plotW;
  // Keep dots off the very top/bottom edges (7% inset each side).
  const yScale = (s: number) =>
    flat
      ? padT + plotH / 2
      : padT + plotH * 0.07 + ((maxScore - s) / (maxScore - minScore)) * plotH * 0.86;

  const midX = xScale(50);
  const midY = padT + plotH / 2;

  const jitter = (s: string) => {
    let h = 0;
    for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) | 0;
    return ((h % 16) - 8) * 1.6;
  };

  return (
    <div className="rounded-2xl border border-[var(--color-border-default)] bg-gradient-to-br from-[var(--color-bg-surface)] to-[var(--color-bg-elevated)]/30 p-2 text-[var(--color-text-primary)]">
      {capped ? (
        <p className="px-2 pt-1 text-[11px] text-[var(--color-text-tertiary)]">
          Plotting {data.length} of {scored.length} plans — Extract Helper yields space so every
          type stays visible
        </p>
      ) : null}
      <div className="relative">
        <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label="Priority versus effort scatter plot" className="h-auto w-full overflow-visible">
          {/* quadrant tints — top-left (high priority / low effort) is the sweet spot */}
          <rect x={padL} y={padT} width={midX - padL} height={midY - padT} fill="currentColor" className="text-[var(--color-success)]/[0.06]" />
          <rect x={midX} y={padT} width={W - padR - midX} height={midY - padT} fill="currentColor" className="text-[var(--color-caution)]/[0.04]" />
          <rect x={padL} y={midY} width={midX - padL} height={H - padB - midY} fill="currentColor" className="text-[var(--color-text-tertiary)]/[0.03]" />
          <rect x={midX} y={midY} width={W - padR - midX} height={H - padB - midY} fill="currentColor" className="text-[var(--color-text-tertiary)]/[0.03]" />

          {/* frame + mid gridlines */}
          <line x1={padL} y1={padT} x2={padL} y2={H - padB} stroke="currentColor" strokeOpacity={0.14} />
          <line x1={padL} y1={H - padB} x2={W - padR} y2={H - padB} stroke="currentColor" strokeOpacity={0.14} />
          <line x1={midX} y1={padT} x2={midX} y2={H - padB} stroke="currentColor" strokeOpacity={0.07} strokeDasharray="4 5" />
          <line x1={padL} y1={midY} x2={W - padR} y2={midY} stroke="currentColor" strokeOpacity={0.07} strokeDasharray="4 5" />

          {/* corner labels */}
          <text x={padL + 8} y={padT + 16} fontSize={13} fontWeight={600} fill="currentColor" opacity={0.55}>Do first</text>
          <text x={W - padR - 8} y={padT + 16} textAnchor="end" fontSize={13} fontWeight={600} fill="currentColor" opacity={0.4}>Big bets</text>
          <text x={padL + 8} y={H - padB - 8} fontSize={12} fill="currentColor" opacity={0.35}>Nice to have</text>
          <text x={W - padR - 8} y={H - padB - 8} textAnchor="end" fontSize={12} fill="currentColor" opacity={0.3}>Heavy lifts</text>

          {/* axes */}
          {(["S", "M", "L", "XL"] as EffortBucket[]).map((b) => (
            <text key={b} x={xScale(EFFORT_X[b])} y={H - padB + 20} fontSize={12} textAnchor="middle" fill="currentColor" opacity={0.5}>
              {b}
            </text>
          ))}
          <text x={W / 2} y={H - 6} fontSize={12} textAnchor="middle" fill="currentColor" opacity={0.45}>Effort →</text>
          <text x={14} y={H / 2} fontSize={12} textAnchor="middle" fill="currentColor" opacity={0.45} transform={`rotate(-90 14 ${H / 2})`}>Priority →</text>

          {data.map((p) => {
            const cx = xScale(EFFORT_X[(p.effort_bucket || "M") as EffortBucket]) + jitter(p.id);
            const cy = yScale(p.rank_score);
            const isHovered = hovered?.id === p.id;
            const accent = typeAccent(p.refactoring_type);
            return (
              <circle
                key={p.id}
                cx={cx}
                cy={cy}
                r={isHovered ? 9 : 6.5}
                fill={accent}
                fillOpacity={isHovered ? 0.95 : 0.72}
                stroke={isHovered ? "var(--color-bg-surface)" : "none"}
                strokeWidth={2}
                className={onSelect ? "cursor-pointer transition-all" : "transition-all"}
                onMouseEnter={() => setHovered(p)}
                onMouseLeave={() => setHovered(null)}
                onClick={onSelect ? () => onSelect(p) : undefined}
              >
                <title>{`${typeMeta(p.refactoring_type).label} · ${p.target_symbol}\n${p.effort_bucket} effort · priority ${p.rank_score.toFixed(2)}`}</title>
              </circle>
            );
          })}
        </svg>
        {hovered ? (
          <div className="pointer-events-none absolute left-3 top-3 max-w-[60%] rounded-lg border border-[var(--color-border-default)] bg-[var(--color-bg-elevated)] px-3 py-2 shadow-md">
            <div className="flex items-center gap-1.5">
              <span className="h-2 w-2 rounded-full" style={{ backgroundColor: typeAccent(hovered.refactoring_type) }} />
              <span className="text-xs font-semibold text-[var(--color-text-primary)]">
                {typeMeta(hovered.refactoring_type).label}
              </span>
            </div>
            <code className="mt-0.5 block truncate text-[11px] text-[var(--color-text-tertiary)]">
              {hovered.target_symbol}
            </code>
          </div>
        ) : null}
      </div>
    </div>
  );
}
