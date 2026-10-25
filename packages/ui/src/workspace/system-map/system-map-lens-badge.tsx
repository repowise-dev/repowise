"use client";

/**
 * A lens mark on a card or a line: ink on the surface plane, an icon and a
 * word ("3 breaking", "cycle", "core"). Never red or amber: health owns those
 * hues on a card, and a second mark in the same vocabulary would read as a
 * second health reading. The icon's shape carries the severity the tone used
 * to, and the lens's results below the map explain it.
 */

import { CircleDot, OctagonAlert, TriangleAlert, type LucideIcon } from "lucide-react";
import type { BadgeTone, SystemMapBadge } from "./types";

const TONE_ICON: Record<BadgeTone, LucideIcon> = {
  danger: OctagonAlert,
  warning: TriangleAlert,
  info: CircleDot,
};

/** Words a screen reader hears before the label, so the icon's meaning is not shape alone. */
const TONE_WORD: Record<BadgeTone, string> = {
  danger: "Lens, high:",
  warning: "Lens, medium:",
  info: "Lens:",
};

export function SystemMapLensBadge({ badge, style }: { badge: SystemMapBadge; style?: React.CSSProperties }) {
  const Icon = TONE_ICON[badge.tone];
  return (
    <span
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: 3,
        padding: "0 5px",
        borderRadius: 4,
        background: "var(--color-bg-surface)",
        border: "1px solid var(--color-text-secondary)",
        color: "var(--color-text-primary)",
        fontFamily: "var(--font-mono, ui-monospace, monospace)",
        fontSize: 10,
        lineHeight: "16px",
        fontWeight: 600,
        whiteSpace: "nowrap",
        ...style,
      }}
    >
      <Icon size={10} aria-hidden style={{ flexShrink: 0 }} />
      <span className="sr-only">{TONE_WORD[badge.tone]} </span>
      {badge.label}
    </span>
  );
}
