"use client";

import * as React from "react";
import { cn } from "../lib/cn";

export interface GraphCanvasShellProps {
  /** Optional one-line title rendered above the canvas (no second header band). */
  title?: string;
  /** Optional one-line description under the title. */
  description?: string;
  /** Right-aligned slot in the title row (e.g. a hint or a single action). */
  titleActions?: React.ReactNode;
  /** A full-width banner slot above the canvas (e.g. truncation notice). */
  banner?: React.ReactNode;
  /** The canvas itself (Sigma / ReactFlow host). Fills the remaining height. */
  children: React.ReactNode;
  /** Overlays drawn on top of the canvas (panels, doc rails, modals). */
  overlay?: React.ReactNode;
  className?: string;
}

/**
 * The single airy canvas container for every graph surface (Communities,
 * Explore, the Knowledge Graph layers route, and Coupling). Replaces the
 * per-view `rounded-lg border` frame and the double-header pattern: it owns at
 * most ONE thin title row and lets the diagram breathe with no box outline or
 * background fill behind it.
 */
export function GraphCanvasShell({
  title,
  description,
  titleActions,
  banner,
  children,
  overlay,
  className,
}: GraphCanvasShellProps) {
  return (
    <div className={cn("flex h-full flex-col", className)}>
      {(title || description || titleActions) && (
        <div className="flex shrink-0 flex-wrap items-start justify-between gap-2 px-4 pt-3 sm:px-6">
          <div className="min-w-0">
            {title && (
              <h2 className="text-sm font-semibold text-[var(--color-text-primary)]">
                {title}
              </h2>
            )}
            {description && (
              <p className="mt-0.5 text-xs text-[var(--color-text-secondary)]">
                {description}
              </p>
            )}
          </div>
          {titleActions && (
            <div className="flex shrink-0 items-center gap-2">{titleActions}</div>
          )}
        </div>
      )}
      {banner && <div className="shrink-0 px-4 pt-3 sm:px-6">{banner}</div>}
      <div className="relative min-h-0 flex-1 overflow-hidden">
        {children}
        {overlay}
      </div>
    </div>
  );
}
