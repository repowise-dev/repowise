"use client";

import { useState, type ReactNode } from "react";
import { ChevronRight, X } from "lucide-react";

/**
 * Secondary filters behind one disclosure that states how many are active
 * while it is closed, plus every active filter as a chip that removes it.
 * The primary axis of a list stays visible beside it; this holds the rest.
 */
export function MoreFilters({
  active,
  label = "More filters",
  children,
}: {
  /** How many of the hidden filters are set. */
  active: number;
  label?: string;
  children: ReactNode;
}) {
  const [open, setOpen] = useState(false);
  return (
    <div className="min-w-0">
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        aria-expanded={open}
        className="inline-flex items-center gap-1 rounded text-xs font-medium text-[var(--color-text-secondary)] hover:text-[var(--color-text-primary)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent-primary)]"
      >
        <ChevronRight aria-hidden="true" className={`h-3.5 w-3.5 transition-transform ${open ? "rotate-90" : ""}`} />
        {label}
        {active > 0 ? <span className="tabular-nums text-[var(--color-text-tertiary)]">({active})</span> : null}
      </button>
      {open ? <div className="mt-3 flex flex-wrap items-center gap-x-5 gap-y-3">{children}</div> : null}
    </div>
  );
}

export interface ActiveFilterChip {
  key: string;
  label: string;
  onRemove: () => void;
}

/** Every active narrowing as a chip that removes it; "Clear all" past one. */
export function ActiveFilterChips({
  chips,
  onClearAll,
  clearAllLabel = "Clear all",
  listLabel = "Active filters",
  removeLabel = (label) => `Remove filter ${label}`,
}: {
  chips: ActiveFilterChip[];
  onClearAll: () => void;
  clearAllLabel?: string;
  listLabel?: string;
  removeLabel?: (label: string) => string;
}) {
  if (chips.length === 0) return null;
  return (
    <ul aria-label={listLabel} className="flex flex-wrap items-center gap-2">
      {chips.map((chip) => (
        <li key={chip.key}>
          <button
            type="button"
            onClick={chip.onRemove}
            aria-label={removeLabel(chip.label)}
            className="inline-flex items-center gap-1 rounded-full border border-[var(--color-border-default)] bg-[var(--color-bg-surface)] px-2.5 py-0.5 text-xs text-[var(--color-text-secondary)] hover:text-[var(--color-text-primary)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent-primary)]"
          >
            {chip.label}
            <X aria-hidden="true" className="h-3 w-3" />
          </button>
        </li>
      ))}
      {chips.length > 1 ? (
        <li>
          <button
            type="button"
            onClick={onClearAll}
            className="rounded text-xs font-medium text-[var(--color-accent-primary)] underline-offset-2 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent-primary)]"
          >
            {clearAllLabel}
          </button>
        </li>
      ) : null}
    </ul>
  );
}
