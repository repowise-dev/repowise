/**
 * Helpers for the confidence score system.
 * Confidence: 0.0 – 1.0 float.
 * Freshness: "fresh" | "stale" | "outdated"
 */

export type FreshnessStatus = "fresh" | "stale" | "outdated";

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
      return "var(--color-confidence-outdated)";
  }
}

/** Tailwind text color class for a given freshness status */
export function statusTextClass(status: FreshnessStatus): string {
  switch (status) {
    case "fresh":
      return "text-green-500";
    case "stale":
      return "text-yellow-500";
    case "outdated":
      return "text-red-500";
  }
}

/** Tailwind bg + text badge classes for a given freshness status */
export function statusBadgeClasses(status: FreshnessStatus): string {
  switch (status) {
    case "fresh":
      return "bg-green-500/10 text-green-500 border-green-500/20";
    case "stale":
      return "bg-yellow-500/10 text-yellow-500 border-yellow-500/20";
    case "outdated":
      return "bg-red-500/10 text-red-500 border-red-500/20";
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
      return "Outdated";
  }
}

/** Color hex for a graph language node */
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

/** Color hex for a graph edge type */
export const EDGE_COLORS: Record<string, string> = {
  imports: "#5B9CF6",
  calls: "#22c55e",
  inherits: "#a855f7",
  implements: "#ec4899",
  co_change: "#8b5cf6",
  co_changes: "#8b5cf6",
};

export function edgeColor(edgeType: string): string {
  return EDGE_COLORS[edgeType.toLowerCase()] ?? "#5B9CF6";
}
