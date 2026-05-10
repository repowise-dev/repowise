"use client";

import { useEffect, useState } from "react";
import { motion } from "framer-motion";
import { HelpCircle } from "lucide-react";

export interface HealthScoreComponent {
  key: string;
  label: string;
  weight: number;
  score: number;
  detail: string;
}

interface HealthScoreRingProps {
  score: number;
  size?: number;
  components?: HealthScoreComponent[];
  /**
   * Optional note rendered at the top of the score breakdown (e.g.
   * to explain that some components were skipped because docs weren't
   * generated). Surfacing the explanation here keeps `index-only` repos
   * from looking unfairly penalized without bloating the ring itself.
   */
  note?: string;
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

export function HealthScoreRing({ score, size = 160, components, note }: HealthScoreRingProps) {
  const [mounted, setMounted] = useState(false);
  const [breakdownOpen, setBreakdownOpen] = useState(false);
  useEffect(() => setMounted(true), []);

  const strokeWidth = 10;
  const radius = (size - strokeWidth) / 2;
  const circumference = 2 * Math.PI * radius;
  const progress = mounted ? (score / 100) * circumference : 0;
  const color = getScoreColor(score);
  const hasBreakdown = components && components.length > 0;

  return (
    <div className="flex flex-col items-center gap-2">
      <div className="relative flex items-center justify-center" style={{ width: size, height: size }}>
        <svg width={size} height={size} className="-rotate-90">
          <circle
            cx={size / 2}
            cy={size / 2}
            r={radius}
            fill="none"
            stroke="var(--color-border-default)"
            strokeWidth={strokeWidth}
          />
          <motion.circle
            cx={size / 2}
            cy={size / 2}
            r={radius}
            fill="none"
            stroke={color}
            strokeWidth={strokeWidth}
            strokeLinecap="round"
            strokeDasharray={circumference}
            initial={{ strokeDashoffset: circumference }}
            animate={{ strokeDashoffset: circumference - progress }}
            transition={{ duration: 1.2, ease: "easeOut" }}
          />
        </svg>
        <div className="absolute inset-0 flex flex-col items-center justify-center">
          <motion.span
            className="text-3xl font-bold tabular-nums"
            style={{ color }}
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            transition={{ delay: 0.3 }}
          >
            {score}
          </motion.span>
          <span className="text-[10px] uppercase tracking-wider text-[var(--color-text-tertiary)]">
            {getScoreLabel(score)}
          </span>
        </div>
      </div>
      {hasBreakdown && (
        <button
          type="button"
          onClick={() => setBreakdownOpen((v) => !v)}
          className="inline-flex items-center gap-1 text-[10px] uppercase tracking-wider text-[var(--color-text-tertiary)] hover:text-[var(--color-accent-primary)] transition"
          aria-expanded={breakdownOpen}
          aria-controls="health-score-breakdown"
        >
          <HelpCircle className="h-3 w-3" aria-hidden />
          {breakdownOpen ? "Hide breakdown" : "Why this score?"}
        </button>
      )}
      {breakdownOpen && hasBreakdown && (
        <div
          id="health-score-breakdown"
          className="w-72 rounded-lg border border-[var(--color-border-default)] bg-[var(--color-bg-elevated)] p-3 text-xs space-y-2"
        >
          <p className="text-[10px] uppercase tracking-wider text-[var(--color-text-tertiary)]">
            Score = weighted average of {components.length} components
          </p>
          {note && (
            <p className="rounded-md border border-[var(--color-border-default)] bg-[var(--color-bg-surface)] px-2 py-1.5 text-[11px] leading-snug text-[var(--color-text-secondary)]">
              {note}
            </p>
          )}
          <ul className="space-y-2">
            {components.map((c) => {
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
