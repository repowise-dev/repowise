"use client";

import * as React from "react";
import { cn } from "../lib/cn";

export interface GraphCanvasShellProps {
  /** Optional one-line title rendered above the canvas (no second header band). */
  title?: string | undefined;
  /** Optional one-line description under the title. */
  description?: string | undefined;
  /** Right-aligned slot in the title row. Scope controls and the toolbar live
   *  here rather than floating over the diagram. */
  titleActions?: React.ReactNode | undefined;
  /** A full-width banner slot above the canvas (e.g. truncation notice). */
  banner?: React.ReactNode | undefined;
  /** The canvas itself (Sigma / ReactFlow host). Fills the remaining height. */
  children: React.ReactNode;
  /**
   * Inspector content. A grid peer of the canvas from `lg` up, so a panel can
   * never land on the map; below `lg` it becomes a bottom sheet over the canvas
   * rather than starving it of height. Render nothing when there is no
   * selection to explain and the canvas takes the full width.
   */
  rail?: React.ReactNode | undefined;
  /** Legend / key row, rendered as a caption under the canvas. */
  footer?: React.ReactNode | undefined;
  /**
   * Drawn on top of the canvas. Reserved for pointer-bound things: context
   * menus, hover cards, modals. Persistent chrome belongs in `titleActions`,
   * `footer` or `rail`.
   */
  overlay?: React.ReactNode | undefined;
  className?: string | undefined;
}

/**
 * The single canvas container for every graph surface.
 *
 * Chrome goes around the map, not on it: controls in the title row, key
 * underneath, inspector as a grid peer. This mirrors `SystemMap`, the Code
 * Health triage view and the Knowledge Graph page, which all converged on the
 * same arrangement.
 */
export function GraphCanvasShell({
  title,
  description,
  titleActions,
  banner,
  children,
  rail,
  footer,
  overlay,
  className,
}: GraphCanvasShellProps) {
  return (
    <div className={cn("flex h-full flex-col", className)}>
      {(title || description || titleActions) && (
        <div className="flex shrink-0 flex-wrap items-start justify-between gap-2 px-4 pt-3 sm:px-6">
          <div className="min-w-0 max-w-2xl">
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
            <div className="flex min-w-0 flex-wrap items-center justify-end gap-2">
              {titleActions}
            </div>
          )}
        </div>
      )}
      {banner && <div className="shrink-0 px-4 pt-3 sm:px-6">{banner}</div>}

      <div
        className={cn(
          "relative mt-3 grid min-h-0 flex-1",
          rail ? "lg:grid-cols-[minmax(0,1fr)_340px]" : "grid-cols-1",
        )}
      >
        {/* A floor, so a tall header or key row cannot squeeze the subject to
            nothing; the page scrolls instead. */}
        <div className="relative min-h-0 overflow-hidden max-lg:min-h-[22rem]">
          {children}
          {overlay}
        </div>
        {rail && (
          <aside
            className={cn(
              // Bottom sheet under lg so the canvas keeps its height on a
              // phone; a real grid column from lg up.
              "absolute inset-x-0 bottom-0 z-20 flex max-h-[70%] flex-col overflow-hidden",
              "rounded-t-xl border-t border-[var(--color-border-default)] bg-[var(--color-bg-surface)] shadow-xl shadow-black/20",
              "lg:static lg:max-h-none lg:rounded-none lg:border-t-0 lg:border-l lg:shadow-none",
            )}
          >
            {rail}
          </aside>
        )}
      </div>

      {footer && <div className="shrink-0 px-4 pb-3 pt-2 sm:px-6">{footer}</div>}
    </div>
  );
}
