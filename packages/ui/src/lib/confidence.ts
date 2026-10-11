/**
 * Helpers for the confidence score system.
 * Confidence: 0.0 – 1.0 float.
 * Freshness: "fresh" | "stale" | "outdated"
 */

import type { FreshnessStatus } from "@repowise-dev/types/docs";
export type { FreshnessStatus };

export function scoreToStatus(score: number): FreshnessStatus {
  if (score >= 0.8) return "fresh";
  if (score >= 0.6) return "stale";
  return "outdated";
}

/** CSS color variable for a given freshness status */
export function statusColor(status: FreshnessStatus): string {
  switch (status) {
    case "fresh":
      return "var(--color-confidence-fresh)";
    case "stale":
      return "var(--color-confidence-stale)";
    case "outdated":
    default:
      return "var(--color-confidence-outdated)";
  }
}

/** Tailwind text color class for a given freshness status (theme-aware) */
export function statusTextClass(status: FreshnessStatus): string {
  switch (status) {
    case "fresh":
      return "text-[var(--color-confidence-fresh)]";
    case "stale":
      return "text-[var(--color-confidence-stale)]";
    case "outdated":
    default:
      return "text-[var(--color-confidence-outdated)]";
  }
}

/** Tailwind bg + text badge classes for a given freshness status (theme-aware) */
export function statusBadgeClasses(status: FreshnessStatus): string {
  switch (status) {
    case "fresh":
      return "bg-[color-mix(in_srgb,var(--color-confidence-fresh)_12%,transparent)] text-[var(--color-confidence-fresh)] border-[color-mix(in_srgb,var(--color-confidence-fresh)_28%,transparent)]";
    case "stale":
      return "bg-[color-mix(in_srgb,var(--color-confidence-stale)_12%,transparent)] text-[var(--color-confidence-stale)] border-[color-mix(in_srgb,var(--color-confidence-stale)_28%,transparent)]";
    case "outdated":
    default:
      return "bg-[color-mix(in_srgb,var(--color-confidence-outdated)_12%,transparent)] text-[var(--color-confidence-outdated)] border-[color-mix(in_srgb,var(--color-confidence-outdated)_28%,transparent)]";
  }
}

/** Human-readable label for freshness */
export function statusLabel(status: FreshnessStatus): string {
  switch (status) {
    case "fresh":
      return "Fresh";
    case "stale":
      return "Stale";
    case "outdated":
    default:
      return "Outdated";
  }
}

/*
 * ALLOWLISTED raw hex (no-raw-hex gate): LANGUAGE_COLORS are canonical
 * language brand colors and EDGE_COLORS feed <canvas>/SVG viz where a CSS
 * var() string cannot resolve — both must be literal hex. Values mirror the
 * --color-lang-* / --color-edge-* tokens in styles/globals.css; keep in sync.
 * Theme-aware canvas tinting (resolving these from CSS vars at runtime) is
 * tracked as follow-up viz work.
 */

/** Color hex for a graph language node (canonical brand hues) */
export const LANGUAGE_COLORS: Record<string, string> = {
  python: "#3776AB",
  typescript: "#3178C6",
  javascript: "#3178C6",
  go: "#00ADD8",
  rust: "#DEA584",
  java: "#ED8B00",
  cpp: "#00599C",
  "c++": "#00599C",
  c: "#00599C",
  config: "#6B7280",
  yaml: "#6B7280",
  dockerfile: "#6B7280",
  other: "#8B5CF6",
};

export function languageColor(lang: string): string {
  return LANGUAGE_COLORS[lang.toLowerCase()] ?? LANGUAGE_COLORS.other ?? "#8B5CF6";
}

/** Color hex for a graph edge type (warm theme palette; mirrors --color-edge-*) */
export const EDGE_COLORS: Record<string, string> = {
  imports: "#F59520",
  calls: "#34D399",
  inherits: "#A98FC4",
  implements: "#C85AA0",
  co_change: "#7C5CC4",
  co_changes: "#7C5CC4",
};

export function edgeColor(edgeType: string): string {
  return EDGE_COLORS[edgeType.toLowerCase()] ?? EDGE_COLORS.imports ?? "#F27F3D";
}
