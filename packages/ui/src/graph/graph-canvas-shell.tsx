"use client";

import * as React from "react";
import { cn } from "../lib/cn";

export interface GraphCanvasShellProps {
  /** "You are here", above the title. Present only once the canvas is showing
   *  something narrower than the whole scope, so it never adds a permanent row. */
  breadcrumb?: React.ReactNode | undefined;
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
  breadcrumb,
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
      {(breadcrumb || title || description || titleActions) && (
        <div className="flex shrink-0 flex-wrap items-start justify-between gap-2 px-4 pt-3 sm:px-6">
          <div className="min-w-0 max-w-2xl">
            {breadcrumb && <div className="mb-1 min-w-0">{breadcrumb}</div>}
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
            nothing; the page scrolls instead.

            Below `lg` the rail is a sheet over the bottom 70% of this cell, and
            the canvas parks its own zoom/fit controls at `bottom-3`. Opening a
            panel therefore buried them. This publishes the sheet's extent as a
            variable the canvas chrome reads for its own offset, so the controls
            step above the sheet instead of under it. Above `lg` the rail is a
            grid peer and there is nothing to clear. */}
        <div
          className={cn(
            "relative min-h-0 overflow-hidden max-lg:min-h-[22rem]",
            rail && "max-lg:[--canvas-chrome-bottom:calc(70%+0.75rem)]",
          )}
        >
          {children}
          {overlay}
        </div>
        {rail && (
          <aside
            className={cn(
              // Bottom sheet under lg so the canvas keeps its height on a
              // phone; a real grid column from lg up.
              // `--z-dropdown`, which is the raw `z-20` this used to carry
              // spelled as the token. Deliberately NOT `--z-sidebar`: Radix
              // tooltips and popovers portal to `body` at `--z-dropdown`, so a
              // rail that outranked them would render its own tooltips behind
              // itself. Equal, and later in the DOM, is what makes them land on
              // top. It still clears the canvas chrome at `--z-elevated`, and
              // still yields to the context menu and the help modal.
              "absolute inset-x-0 bottom-0 z-[var(--z-dropdown)] flex max-h-[70%] flex-col overflow-hidden",
              "rounded-t-xl border-t border-[var(--color-border-default)] bg-[var(--color-bg-surface)] shadow-xl shadow-black/20",
              // `h-0 min-h-full`: fill the row without contributing to its
              // height, so a tall panel scrolls instead of stretching the canvas.
              "lg:static lg:h-0 lg:min-h-full lg:max-h-none lg:rounded-none lg:border-t-0 lg:border-l lg:shadow-none",
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
