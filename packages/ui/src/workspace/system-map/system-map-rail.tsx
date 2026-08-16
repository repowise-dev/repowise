"use client";

/**
 * Shared shell for everything in the System Map's rail: the inspector and the
 * blast-radius / breaking-change / conformance panels.
 *
 * These were four independently-written `position: absolute` cards floating on
 * the canvas, three of which claimed the same top-right corner and physically
 * stacked on each other whenever a selection and an overlay were both active.
 * A diagram is the one thing on its page that cannot be read past, so the rail
 * is now a grid peer of the canvas (see `SystemMap`) and a panel is just a card
 * in a column. Nothing here positions itself; the rail owns placement and the
 * single scroll region, so panels never grow their own nested scrollbar.
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

/** Label/value row used by the inspector's fact lists. */
export function RailField({
  label,
  value,
}: {
  label: string;
  value: React.ReactNode;
}) {
  return (
    <div className="flex justify-between gap-3 py-[3px]">
      <span className="text-[var(--color-text-tertiary)]">{label}</span>
      <span className="break-words text-right text-[var(--color-text-primary)]">
        {value}
      </span>
    </div>
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
