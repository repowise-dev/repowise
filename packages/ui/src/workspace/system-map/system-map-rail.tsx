"use client";

/**
 * Card shell for the blast-radius, breaking-change and conformance rail panels.
 * The OSS map lists lens results below the canvas instead; these stay for
 * hosts that still render a rail beside it. `RailChip` is also used by
 * `BreakingChangeRow`.
 */

import { X } from "lucide-react";
import { Card } from "../../ui/card";

export interface SystemMapRailPanelProps {
  /** Small-caps panel identity, e.g. "Blast radius". */
  eyebrow: string;
  /** Optional leading glyph, already coloured by the caller. */
  icon?: React.ReactNode;
  /** One-line status under the header (counts, "not yet checked", errors). */
  summary?: React.ReactNode;
  onClear: () => void;
  /** Accessible name for the dismiss button, e.g. "Clear blast radius". */
  clearLabel: string;
  children?: React.ReactNode;
}

/** Small-caps eyebrow, the rail's one label idiom. */
export function RailEyebrow({ children }: { children: React.ReactNode }) {
  return (
    <span className="text-[10px] font-bold uppercase tracking-[0.06em] text-[var(--color-text-tertiary)]">
      {children}
    </span>
  );
}

/**
 * Severity chip. Replaces four hand-rolled copies of the same colour-mix pill.
 * The caller passes the token so the chip stays in whatever colour band its
 * feature owns.
 */
export function RailChip({
  color,
  children,
}: {
  color: string;
  children: React.ReactNode;
}) {
  return (
    <span
      className="shrink-0 rounded-[var(--radius-sm)] px-1.5 py-px text-[9px] font-bold uppercase"
      style={{
        color,
        border: `1px solid color-mix(in srgb, ${color} 45%, transparent)`,
        background: `color-mix(in srgb, ${color} 16%, transparent)`,
      }}
    >
      {children}
    </span>
  );
}

export function SystemMapRailPanel({
  eyebrow,
  icon,
  summary,
  onClear,
  clearLabel,
  children,
}: SystemMapRailPanelProps) {
  return (
    <Card className="overflow-hidden bg-[var(--color-bg-elevated)] text-xs text-[var(--color-text-secondary)] shadow-[var(--shadow-lg)]">
      <div className="flex items-center justify-between gap-2 border-b border-[var(--color-border-default)] px-3 py-2.5">
        <span className="inline-flex min-w-0 items-center gap-1.5">
          {icon}
          <RailEyebrow>{eyebrow}</RailEyebrow>
        </span>
        <button
          type="button"
          onClick={onClear}
          aria-label={clearLabel}
          className="inline-flex shrink-0 cursor-pointer text-[var(--color-text-tertiary)] hover:text-[var(--color-text-primary)]"
        >
          <X size={14} />
        </button>
      </div>

      {summary !== undefined && (
        <div className="border-b border-[var(--color-border-default)] px-3 py-2 text-[11px] text-[var(--color-text-tertiary)]">
          {summary}
        </div>
      )}

      {children}
    </Card>
  );
}
