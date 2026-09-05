/**
 * Shared color tokens + helpers for the code-health surface.
 *
 * Single source of truth so the score pill on a file row, the severity
 * chip on a finding card, and the KPI card text colors all agree.
 * All colors come from the semantic CSS tokens (--color-error/warning/
 * caution/success) so the surface themes correctly in both modes.
 */

import { bandForScore, type HealthBand } from "@repowise-dev/types/health";

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
 * Literal strings so Tailwind's static scanner keeps them.
 *
 * These MUST use the same tokens as `HEALTH_BAND_INK` below. They did not:
 * the Warning band read `--color-caution` here and `--color-warning` there,
 * so a 6.9 was one amber as a label and a different amber as a fill —
 * the treemap tile and the file page's score, or the zoom map's dot and its
 * detail panel, describing one file in one viewport. Two marks on one object
 * may not disagree about a colour they both derive from `bandForScore`.
 * `--color-caution` stays where it belongs: the four-step severity/presentation
 * ramp below, which is not a band. */
const HEALTH_BAND_TEXT: Record<HealthBand, string> = {
  alert: "text-[var(--color-error)]",
  warning: "text-[var(--color-warning)]",
  healthy: "text-[var(--color-success)]",
};

const HEALTH_BAND_BADGE_SOFT: Record<HealthBand, string> = {
  alert: "bg-[var(--color-error)]/15 text-[var(--color-error)]",
  warning: "bg-[var(--color-warning)]/15 text-[var(--color-warning)]",
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

/* Raw CSS custom-property references per canonical band, for SVG/canvas
 * fills and inline styles where a class string cannot be used. */
const HEALTH_BAND_INK: Record<HealthBand, string> = {
  alert: "var(--color-error)",
  warning: "var(--color-warning)",
  healthy: "var(--color-success)",
};

/**
 * A canonical band as an ink color, for a key or legend that has a band but no
 * score to derive it from. Exported so an off-canvas key paints from the same
 * table the fills do — a legend with its own copy of the colours is a legend
 * that can describe a canvas it no longer matches.
 */
export function healthBandInk(band: HealthBand): string {
  return HEALTH_BAND_INK[band];
}

/**
 * Canonical banding for a higher-is-better health score on the 0-10 scale,
 * as an ink color usable in `style`/SVG attributes. Thresholds come from the
 * shared `bandForScore` mirror so every surface agrees on what counts as red.
 */
export function healthInk(score10: number): string {
  return healthBandInk(bandForScore(score10));
}

/** `healthInk` for scores expressed on a 0-100 scale. */
export function healthInk100(score100: number): string {
  return healthInk(score100 / 10);
}

/** `bandForScore` for scores expressed on a 0-100 scale. */
export function healthBand100(score100: number): HealthBand {
  return bandForScore(score100 / 10);
}

/**
 * Banding for a higher-is-worse risk value on the 0-1 scale, as an ink
 * color. Matches the impact-graph node banding (>=0.66 alert, >=0.33
 * warning) so risk reads the same across tables, charts, and graphs.
 */
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
