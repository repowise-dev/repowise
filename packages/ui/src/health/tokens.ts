/**
 * Shared color tokens + helpers for the code-health surface.
 *
 * Single source of truth so the score pill on a file row, the severity
 * chip on a finding card, and the KPI card text colors all agree.
 */

export type Severity = "critical" | "high" | "medium" | "low";

export const SEVERITY_ORDER: Record<Severity, number> = {
  low: 0,
  medium: 1,
  high: 2,
  critical: 3,
};

export const SEVERITY_LABEL: Record<Severity, string> = {
  critical: "Critical",
  high: "High",
  medium: "Medium",
  low: "Low",
};

export const SEVERITY_CHIP: Record<Severity, string> = {
  critical: "bg-red-500/15 text-red-500 border border-red-500/30",
  high: "bg-amber-500/15 text-amber-500 border border-amber-500/30",
  medium: "bg-yellow-500/15 text-yellow-600 border border-yellow-500/30",
  low: "bg-zinc-500/15 text-zinc-500 border border-zinc-500/30",
};

export const SEVERITY_BAR: Record<Severity, string> = {
  critical: "bg-red-500",
  high: "bg-amber-500",
  medium: "bg-yellow-500",
  low: "bg-zinc-500",
};

export function scoreTextColor(score: number | null | undefined): string {
  if (score == null) return "text-[var(--color-text-primary)]";
  if (score < 4) return "text-red-500";
  if (score < 7) return "text-amber-500";
  return "text-emerald-500";
}

export function scoreBadgeClass(score: number): string {
  if (score < 4) return "bg-red-500/15 text-red-500 border border-red-500/30";
  if (score < 7) return "bg-amber-500/15 text-amber-500 border border-amber-500/30";
  return "bg-emerald-500/15 text-emerald-500 border border-emerald-500/30";
}

export function coverageColor(pct: number): string {
  if (pct < 30) return "bg-red-500";
  if (pct < 60) return "bg-amber-500";
  if (pct < 80) return "bg-yellow-400";
  return "bg-emerald-500";
}

export function deltaColor(delta: number | null | undefined): string {
  if (delta == null || delta === 0) return "text-[var(--color-text-tertiary)]";
  return delta > 0 ? "text-emerald-500" : "text-red-500";
}

export function formatDelta(delta: number | null | undefined): string {
  if (delta == null) return "—";
  const sign = delta > 0 ? "+" : "";
  return `${sign}${delta.toFixed(2)}`;
}
