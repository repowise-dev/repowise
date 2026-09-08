/**
 * Shared color tokens + helpers for the code-health surface.
 *
 * Single source of truth so the score pill on a file row, the severity
 * chip on a finding card, and the KPI card text colors all agree.
 * All colors come from the semantic CSS tokens (--color-error/warning/
 * caution/success) so the surface themes correctly in both modes.
 */

import {
  bandForScore,
  HEALTH_BAND_LABEL,
  type HealthBand,
} from "@repowise-dev/types/health";

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

/**
 * @deprecated Use `SeverityMark`. This paints a tinted ground, a border and
 * coloured text for one token that repeats several times per row, and in a
 * findings list the grounds tile into stripes that outweigh the marker names
 * beside them. Kept exported because `@repowise-dev/ui` is consumed outside
 * this repo; nothing in here should reach for it.
 */
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
 * The one health band -> colour vocabulary. Excellent and Good share the
 * green; the band word carries the difference. Every table below is written
 * out in full because Tailwind only sees class names it can read literally.
 */
const HEALTH_BAND_VAR: Record<HealthBand, string> = {
  excellent: "var(--color-success)",
  good: "var(--color-success)",
  fair: "var(--color-caution)",
  needs_work: "var(--color-warning)",
  at_risk: "var(--color-error)",
};

const HEALTH_BAND_TEXT: Record<HealthBand, string> = {
  excellent: "text-[var(--color-success)]",
  good: "text-[var(--color-success)]",
  fair: "text-[var(--color-caution)]",
  needs_work: "text-[var(--color-warning)]",
  at_risk: "text-[var(--color-error)]",
};

const HEALTH_BAND_BADGE: Record<HealthBand, string> = {
  excellent:
    "bg-[var(--color-success)]/15 text-[var(--color-success)] border border-[var(--color-success)]/30",
  good: "bg-[var(--color-success)]/15 text-[var(--color-success)] border border-[var(--color-success)]/30",
  fair: "bg-[var(--color-caution)]/15 text-[var(--color-caution)] border border-[var(--color-caution)]/30",
  needs_work:
    "bg-[var(--color-warning)]/15 text-[var(--color-warning)] border border-[var(--color-warning)]/30",
  at_risk:
    "bg-[var(--color-error)]/15 text-[var(--color-error)] border border-[var(--color-error)]/30",
};

const HEALTH_BAND_BADGE_SOFT: Record<HealthBand, string> = {
  excellent: "bg-[var(--color-success)]/15 text-[var(--color-success)]",
  good: "bg-[var(--color-success)]/15 text-[var(--color-success)]",
  fair: "bg-[var(--color-caution)]/15 text-[var(--color-caution)]",
  needs_work: "bg-[var(--color-warning)]/15 text-[var(--color-warning)]",
  at_risk: "bg-[var(--color-error)]/15 text-[var(--color-error)]",
};

/** SVG `fill-` classes, for the marks the scatter charts draw. */
export const HEALTH_BAND_FILL: Record<HealthBand, string> = {
  excellent: "fill-[var(--color-success)]",
  good: "fill-[var(--color-success)]",
  fair: "fill-[var(--color-caution)]",
  needs_work: "fill-[var(--color-warning)]",
  at_risk: "fill-[var(--color-error)]",
};

/**
 * Bar fills for the distribution. Good is the same green stepped down in
 * opacity, so it stays legible where it sits next to Excellent.
 */
export const HEALTH_BAND_BAR: Record<HealthBand, string> = {
  excellent: "bg-[var(--color-success)]",
  good: "bg-[var(--color-success)]/55",
  fair: "bg-[var(--color-caution)]",
  needs_work: "bg-[var(--color-warning)]",
  at_risk: "bg-[var(--color-error)]",
};

export function healthBandColor(band: HealthBand): string {
  return HEALTH_BAND_VAR[band];
}

export function healthBandTextColor(band: HealthBand): string {
  return HEALTH_BAND_TEXT[band];
}

export function healthBandSoftBadgeClass(band: HealthBand): string {
  return HEALTH_BAND_BADGE_SOFT[band];
}

/** Tailwind text colour for a 1-10 score. */
export function scoreTextColor(score: number | null | undefined): string {
  if (score == null) return "text-[var(--color-text-primary)]";
  return HEALTH_BAND_TEXT[bandForScore(score)];
}

/** Bordered score pill, for a figure sitting in a table row. */
export function scoreBadgeClass(score: number): string {
  return HEALTH_BAND_BADGE[bandForScore(score)];
}

/** Borderless score pill, for a figure sitting inline in prose. */
export function scoreSoftBadgeClass(score: number): string {
  return HEALTH_BAND_BADGE_SOFT[bandForScore(score)];
}

/** Raw colour for a 1-10 score, for canvas ink and inline styles. */
export function healthInk(score10: number): string {
  return HEALTH_BAND_VAR[bandForScore(score10)];
}

/** The same, for the surfaces that carry health on a 0-100 scale. */
export function healthInk100(score100: number): string {
  return healthInk(score100 / 10);
}

export function healthBand100(score100: number): HealthBand {
  return bandForScore(score100 / 10);
}

/**
 * A 1-10 score as the colour and the word together.
 *
 * One function for the same reason `coverageBand()` is one function: a lede
 * prints the label, the figure beside it takes the colour, and two call sites
 * disagreeing about where "Good" starts is exactly the drift this replaces.
 */
export function healthBand(score: number): { color: string; label: string } {
  const band = bandForScore(score);
  return { color: HEALTH_BAND_VAR[band], label: HEALTH_BAND_LABEL[band] };
}

export function riskInk(risk01: number): string {
  if (risk01 >= 0.66) return "var(--color-error)";
  if (risk01 >= 0.33) return "var(--color-warning)";
  return "var(--color-success)";
}

export function coverageColor(pct: number): string {
  if (pct < 30) return "bg-[var(--color-error)]";
  if (pct < 60) return "bg-[var(--color-warning)]";
  if (pct < 80) return "bg-[var(--color-caution)]";
  return "bg-[var(--color-success)]";
}

/**
 * Coverage as a band, at the thresholds {@link coverageColor} already paints.
 *
 * One function for the same reason `healthBand()` is one function: the coverage
 * lede prints the label, the figure takes the colour and the distribution bar
 * segments by it, and three call sites disagreeing about where "Strong" starts
 * is worse than the duplication that avoids it.
 */
export function coverageBand(pct: number): { color: string; label: string } {
  if (pct < 30) return { color: "var(--color-error)", label: "Thin" };
  if (pct < 60) return { color: "var(--color-warning)", label: "Partial" };
  if (pct < 80) return { color: "var(--color-caution)", label: "Solid" };
  return { color: "var(--color-success)", label: "Strong" };
}

/** Tailwind text colour for a coverage figure, on the same bands. */
export function coverageTextColor(pct: number | null | undefined): string {
  if (pct == null) return "text-[var(--color-text-primary)]";
  if (pct < 30) return "text-[var(--color-error)]";
  if (pct < 60) return "text-[var(--color-warning)]";
  if (pct < 80) return "text-[var(--color-caution)]";
  return "text-[var(--color-success)]";
}

export function deltaColor(delta: number | null | undefined): string {
  if (delta == null || delta === 0) return "text-[var(--color-text-tertiary)]";
  return delta > 0 ? "text-[var(--color-success)]" : "text-[var(--color-error)]";
}

export function formatDelta(delta: number | null | undefined): string {
  if (delta == null) return "—";
  // Rounding decides the sign here, not the raw value: a delta of -0.001 is
  // "0.00", and printing it as "-0.00" claims a direction the figure on screen
  // does not have.
  const rounded = Number(delta.toFixed(2));
  if (rounded === 0) return "0.00";
  return `${rounded > 0 ? "+" : ""}${rounded.toFixed(2)}`;
}

/**
 * A finding's deduction as a signed string, or `null` when it has none.
 *
 * Two different zeros, kept apart. Performance findings carry an impact of
 * exactly zero by construction, so there is no deduction to print and `null`
 * tells the caller to print a word instead. A real deduction too small to show
 * at two places is still a measured cost, so it prints as a bound rather than
 * as "−0.00", which would claim the finding costs nothing.
 */
export function formatHealthImpact(impact: number | null | undefined): string | null {
  if (impact == null || impact === 0) return null;
  const rounded = Number(Math.abs(impact).toFixed(2));
  if (rounded === 0) return "−<0.01";
  return `−${rounded.toFixed(2)}`;
}
