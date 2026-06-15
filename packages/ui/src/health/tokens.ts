/**
 * Shared color tokens + helpers for the code-health surface.
 *
 * Single source of truth so the score pill on a file row, the severity
 * chip on a finding card, and the KPI card text colors all agree.
 * All colors come from the semantic CSS tokens (--color-error/warning/
 * caution/success) so the surface themes correctly in both modes.
 */

import type { HealthBand } from "@repowise-dev/types/health";

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
  critical:
    "bg-[var(--color-error)]/15 text-[var(--color-error)] border border-[var(--color-error)]/30",
  high: "bg-[var(--color-warning)]/15 text-[var(--color-warning)] border border-[var(--color-warning)]/30",
  medium:
    "bg-[var(--color-caution)]/15 text-[var(--color-caution)] border border-[var(--color-caution)]/30",
  low: "bg-[var(--color-text-tertiary)]/15 text-[var(--color-text-tertiary)] border border-[var(--color-text-tertiary)]/30",
};

export const SEVERITY_BAR: Record<Severity, string> = {
  critical: "bg-[var(--color-error)]",
  high: "bg-[var(--color-warning)]",
  medium: "bg-[var(--color-caution)]",
  low: "bg-[var(--color-text-tertiary)]",
};

/**
 * Internal 4-step COLOR RAMP for score pills. This is presentation granularity
 * only — NOT a labeling scheme. The canonical, defect-backed health *buckets*
 * are the 3 `HealthBand` values (Healthy/Warning/Alert) defined once in
 * `@repowise-dev/types/health` (mirroring core `grading.py`); use those for any
 * surfaced band label or count. `scoreBand` keeps an extra step (poor vs fair
 * inside the Warning band) purely so the file-table pills read on a finer ramp.
 */
export type ScoreBand = "critical" | "poor" | "fair" | "good";

export function scoreBand(score: number): ScoreBand {
  if (score < 4) return "critical";
  if (score < 6) return "poor";
  if (score < 8) return "fair";
  return "good";
}

/* Color classes for the 3 canonical health bands (Alert/Warning/Healthy).
 * Literal strings so Tailwind's static scanner keeps them. */
const HEALTH_BAND_TEXT: Record<HealthBand, string> = {
  alert: "text-[var(--color-error)]",
  warning: "text-[var(--color-caution)]",
  healthy: "text-[var(--color-success)]",
};

const HEALTH_BAND_BADGE_SOFT: Record<HealthBand, string> = {
  alert: "bg-[var(--color-error)]/15 text-[var(--color-error)]",
  warning: "bg-[var(--color-caution)]/15 text-[var(--color-caution)]",
  healthy: "bg-[var(--color-success)]/15 text-[var(--color-success)]",
};

/** Band → soft badge class. Pass the API-provided band where available; falls
 * back to deriving it from a score via the shared `bandForScore` mirror. */
export function healthBandSoftBadgeClass(band: HealthBand): string {
  return HEALTH_BAND_BADGE_SOFT[band];
}

export function healthBandTextColor(band: HealthBand): string {
  return HEALTH_BAND_TEXT[band];
}

/* Literal class strings per band so Tailwind's static scanner sees them. */
const BAND_TEXT: Record<ScoreBand, string> = {
  critical: "text-[var(--color-error)]",
  poor: "text-[var(--color-warning)]",
  fair: "text-[var(--color-caution)]",
  good: "text-[var(--color-success)]",
};

const BAND_BADGE: Record<ScoreBand, string> = {
  critical:
    "bg-[var(--color-error)]/15 text-[var(--color-error)] border border-[var(--color-error)]/30",
  poor: "bg-[var(--color-warning)]/15 text-[var(--color-warning)] border border-[var(--color-warning)]/30",
  fair: "bg-[var(--color-caution)]/15 text-[var(--color-caution)] border border-[var(--color-caution)]/30",
  good: "bg-[var(--color-success)]/15 text-[var(--color-success)] border border-[var(--color-success)]/30",
};

const BAND_BADGE_SOFT: Record<ScoreBand, string> = {
  critical: "bg-[var(--color-error)]/15 text-[var(--color-error)]",
  poor: "bg-[var(--color-warning)]/15 text-[var(--color-warning)]",
  fair: "bg-[var(--color-caution)]/15 text-[var(--color-caution)]",
  good: "bg-[var(--color-success)]/15 text-[var(--color-success)]",
};

export function scoreTextColor(score: number | null | undefined): string {
  if (score == null) return "text-[var(--color-text-primary)]";
  return BAND_TEXT[scoreBand(score)];
}

/** Bordered score pill (file table, KPI badges). */
export function scoreBadgeClass(score: number): string {
  return BAND_BADGE[scoreBand(score)];
}

/** Borderless compact variant (inline HealthBadge next to file paths). */
export function scoreSoftBadgeClass(score: number): string {
  return BAND_BADGE_SOFT[scoreBand(score)];
}

export function coverageColor(pct: number): string {
  if (pct < 30) return "bg-[var(--color-error)]";
  if (pct < 60) return "bg-[var(--color-warning)]";
  if (pct < 80) return "bg-[var(--color-caution)]";
  return "bg-[var(--color-success)]";
}

export function deltaColor(delta: number | null | undefined): string {
  if (delta == null || delta === 0) return "text-[var(--color-text-tertiary)]";
  return delta > 0 ? "text-[var(--color-success)]" : "text-[var(--color-error)]";
}

export function formatDelta(delta: number | null | undefined): string {
  if (delta == null) return "—";
  const sign = delta > 0 ? "+" : "";
  return `${sign}${delta.toFixed(2)}`;
}
