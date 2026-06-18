"use client";

import { useEffect, useRef, useState } from "react";
import { HelpCircle } from "lucide-react";
import type { HealthScoreComponent } from "./health-score-ring";

export interface HealthSparklinePoint {
  taken_at: string | null;
  /** Code-health average on the 1–10 scale. */
  average_health: number;
}

interface HealthScoreBadgeProps {
  /** Composite repo score, 0–100. */
  score: number;
  components?: HealthScoreComponent[];
  note?: string;
  /** Code-health snapshot history rendered as a small trend sparkline. */
  history?: HealthSparklinePoint[];
}

function getScoreColor(score: number): string {
  if (score >= 75) return "var(--color-success)";
  if (score >= 50) return "var(--color-warning)";
  return "var(--color-error)";
}

function getScoreLabel(score: number): string {
  if (score >= 80) return "Excellent";
  if (score >= 65) return "Good";
  if (score >= 50) return "Fair";
  if (score >= 30) return "Needs Work";
  return "Critical";
}

function Sparkline({ points }: { points: HealthSparklinePoint[] }) {
  const w = 64;
  const h = 20;
  const values = points.map((p) => p.average_health);
  const min = Math.min(...values);
  const max = Math.max(...values);
  const span = max - min || 1;
  const step = values.length > 1 ? w / (values.length - 1) : w;
  const path = values
    .map((v, i) => {
      const x = i * step;
      const y = h - 2 - ((v - min) / span) * (h - 4);
      return `${i === 0 ? "M" : "L"}${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(" ");
  const latest = values[values.length - 1] ?? 0;
  return (
    <svg
      width={w}
      height={h}
      className="shrink-0"
      role="img"
      aria-label={`Code health trend, latest ${latest.toFixed(1)}/10`}
    >
      <title>{`Code health (avg) trend — latest ${latest.toFixed(1)}/10`}</title>
      <path
        d={path}
        fill="none"
        stroke="var(--color-accent-primary)"
        strokeWidth={1.5}
        strokeLinecap="round"
      />
    </svg>
  );
}

/**
 * Compact header twin of `HealthScoreRing`: score chip + label, an optional
 * code-health trend sparkline, and the same "Why this score?" breakdown in a
 * click-toggled dropdown. Used where the full ring would dominate the layout.
 */
export function HealthScoreBadge({ score, components, note, history }: HealthScoreBadgeProps) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);
  const color = getScoreColor(score);
  const hasBreakdown = !!components && components.length > 0;

  useEffect(() => {
    if (!open) return;
    const onPointerDown = (e: PointerEvent) => {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("pointerdown", onPointerDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("pointerdown", onPointerDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  return (
    <div ref={rootRef} className="relative inline-flex items-center gap-2">
      <button
        type="button"
        onClick={() => hasBreakdown && setOpen((v) => !v)}
        aria-expanded={open}
        aria-haspopup={hasBreakdown ? "dialog" : undefined}
        className={`inline-flex items-center gap-2 rounded-full border border-[var(--color-border-default)] bg-[var(--color-bg-elevated)] px-2.5 py-1 ${
          hasBreakdown ? "hover:border-[var(--color-accent-primary)] transition-colors" : "cursor-default"
        }`}
      >
        <span className="text-sm font-bold tabular-nums" style={{ color }}>
          {Math.round(score)}
        </span>
        <span className="text-[10px] uppercase tracking-wider text-[var(--color-text-tertiary)]">
          {getScoreLabel(score)}
        </span>
        {history && history.length >= 2 && <Sparkline points={history} />}
        {hasBreakdown && (
          <HelpCircle className="h-3 w-3 text-[var(--color-text-tertiary)]" aria-hidden />
        )}
      </button>

      {open && hasBreakdown && (
        <div
          role="dialog"
          aria-label="Score breakdown"
          className="absolute left-0 top-full z-30 mt-2 w-72 rounded-lg border border-[var(--color-border-default)] bg-[var(--color-bg-elevated)] p-3 text-xs space-y-2 shadow-lg"
        >
          <p className="text-[10px] uppercase tracking-wider text-[var(--color-text-tertiary)]">
            Score = weighted average of {components!.length} components
          </p>
          {note && (
            <p className="rounded-md border border-[var(--color-border-default)] bg-[var(--color-bg-surface)] px-2 py-1.5 text-xs leading-snug text-[var(--color-text-secondary)]">
              {note}
            </p>
          )}
          <ul className="space-y-2">
            {components!.map((c) => {
              const barColor =
                c.score >= 75
                  ? "var(--color-success)"
                  : c.score >= 50
                    ? "var(--color-warning)"
                    : "var(--color-error)";
              return (
                <li key={c.key} className="space-y-1">
                  <div className="flex items-baseline justify-between gap-3">
                    <span className="text-[var(--color-text-primary)]">
                      {c.label}{" "}
                      <span className="text-[var(--color-text-tertiary)]">
                        ({Math.round(c.weight * 100)}%)
                      </span>
                    </span>
                    <span className="tabular-nums text-[var(--color-text-secondary)]">
                      {Math.round(c.score)}/100
                    </span>
                  </div>
                  <div className="h-1 w-full overflow-hidden rounded-full bg-[var(--color-bg-surface)]">
                    <div
                      className="h-full transition-[width] duration-300"
                      style={{ width: `${c.score}%`, background: barColor }}
                    />
                  </div>
                  {c.detail && (
                    <p className="text-[10px] text-[var(--color-text-tertiary)]">{c.detail}</p>
                  )}
                </li>
              );
            })}
          </ul>
        </div>
      )}
    </div>
  );
}
