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
 * The Map has two levels and passes a root and a leaf. A deeper surface (the
 * System Map's Estate / Group / Service) passes the crumbs between them as
 * `trail`, each one a way back to that level.
 */
export interface GraphScopeCrumb {
  label: string;
  onClick: () => void;
}

export function GraphScopeBreadcrumb({
  rootLabel,
  leafLabel,
  onRoot,
  trail = [],
}: {
  /** The whole-repo crumb, e.g. the repo name. */
  rootLabel: string;
  /** Where you are now. */
  leafLabel: string;
  onRoot: () => void;
  /** Levels between the root and the leaf, outermost first. */
  trail?: readonly GraphScopeCrumb[];
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
        className="flex shrink-0 items-center gap-1 rounded px-1.5 py-1 hover:bg-[var(--color-bg-elevated)] hover:text-[var(--color-text-primary)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-focus-ring)]"
      >
        <Home className="h-3.5 w-3.5" />
        <span className="max-w-[10rem] truncate">{rootLabel}</span>
      </button>
      {trail.map((crumb, i) => (
        <span key={`${i}:${crumb.label}`} className="flex min-w-0 items-center gap-1">
          <ChevronRight className="h-3.5 w-3.5 shrink-0 text-[var(--color-text-tertiary)]" />
          <button
            type="button"
            onClick={crumb.onClick}
            title={`Back to ${crumb.label}`}
            className="max-w-[12rem] truncate rounded px-1.5 py-1 hover:bg-[var(--color-bg-elevated)] hover:text-[var(--color-text-primary)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-focus-ring)]"
          >
            {crumb.label}
          </button>
        </span>
      ))}
      <ChevronRight className="h-3.5 w-3.5 shrink-0 text-[var(--color-text-tertiary)]" />
      <span aria-current="location" className="max-w-[16rem] truncate px-1 py-1 font-medium text-[var(--color-text-primary)]">
        {leafLabel}
      </span>
    </nav>
  );
}
