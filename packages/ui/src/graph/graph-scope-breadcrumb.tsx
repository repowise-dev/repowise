"use client";

import { ChevronRight, Home } from "lucide-react";

/**
 * "You are here" for the graph, shown only once you have gone somewhere.
 *
 * Entering a community swaps what the canvas draws, so without this the reader
 * has no way to tell a scoped graph from the whole repo and no way back except
 * a keyboard shortcut nothing documents. Follows `ZoomBreadcrumb` on the
 * Knowledge Graph: a home crumb that returns to the overview, then the trail.
 *
 * Two levels is the whole depth the Map has, so this takes a single leaf rather
 * than a chain it would never fill.
 */
export function GraphScopeBreadcrumb({
  rootLabel,
  leafLabel,
  onRoot,
}: {
  /** The whole-repo crumb, e.g. the repo name. */
  rootLabel: string;
  /** Where you are now. */
  leafLabel: string;
  onRoot: () => void;
}) {
  return (
    <nav
      aria-label="Graph location"
      className="flex min-w-0 items-center gap-1 text-xs text-[var(--color-text-secondary)]"
    >
      <button
        type="button"
        onClick={onRoot}
        title={`Back to ${rootLabel}`}
        className="flex shrink-0 items-center gap-1 rounded px-1.5 py-1 hover:bg-[var(--color-bg-elevated)] hover:text-[var(--color-text-primary)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent-primary)]"
      >
        <Home className="h-3.5 w-3.5" />
        <span className="max-w-[10rem] truncate">{rootLabel}</span>
      </button>
      <ChevronRight className="h-3.5 w-3.5 shrink-0 text-[var(--color-text-tertiary)]" />
      <span className="max-w-[16rem] truncate px-1 py-1 font-medium text-[var(--color-text-primary)]">
        {leafLabel}
      </span>
    </nav>
  );
}
