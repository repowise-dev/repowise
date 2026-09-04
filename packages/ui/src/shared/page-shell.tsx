import * as React from "react";
import { cn } from "../lib/cn";

/**
 * The frame's own geometry, exported so a loading placeholder can reserve the
 * exact same box (see `PageSkeleton`). A literal copied into a skeleton is a
 * reflow waiting to happen; sharing the string makes drift impossible.
 */
export const PAGE_SHELL_CONTAINER =
  "mx-auto w-full p-[var(--page-pad)] space-y-[var(--section-gap)]";
export const PAGE_SHELL_MAX_WIDTH = {
  default: "max-w-[1280px]",
  wide: "max-w-[1600px]",
} as const;
export const PAGE_SHELL_HEADER =
  "flex flex-wrap items-start justify-between gap-4";
/** The h1's type styles, so a placeholder inherits the real line box. */
export const PAGE_SHELL_TITLE =
  "flex items-center gap-2 text-xl font-semibold text-[var(--color-text-primary)]";
/** The description's type styles, including the readable-measure cap. */
export const PAGE_SHELL_DESCRIPTION =
  "max-w-[68ch] text-sm text-[var(--color-text-secondary)]";

export interface PageShellProps {
  title: string;
  icon?: React.ReactNode;
  description?: string;
  /** Right-aligned action slot in the header band. */
  actions?: React.ReactNode;
  /** `default` ~1280px for reading surfaces; `wide` ~1600px for canvases. */
  maxWidth?: "default" | "wide";
  className?: string;
  children?: React.ReactNode;
}

/**
 * The single page frame: outer padding, centred max width, vertical rhythm,
 * and one header band (title + optional icon + one-line description + actions).
 * Replaces the hand-rolled per-page headers.
 */
export function PageShell({
  title,
  icon,
  description,
  actions,
  maxWidth = "default",
  className,
  children,
}: PageShellProps) {
  return (
    <div
      className={cn(
        PAGE_SHELL_CONTAINER,
        PAGE_SHELL_MAX_WIDTH[maxWidth],
        className,
      )}
    >
      <header className={PAGE_SHELL_HEADER}>
        <div className="min-w-0 space-y-1">
          <h1 className={PAGE_SHELL_TITLE}>
            {icon}
            {title}
          </h1>
          {description && (
            // Capped at a readable measure. Unbounded, this ran the full
            // 1280 on a wide viewport, which is roughly 160 characters.
            <p className={cn(PAGE_SHELL_DESCRIPTION, "[text-wrap:pretty]")}>
              {description}
            </p>
          )}
        </div>
        {actions && <div className="flex shrink-0 items-center gap-2">{actions}</div>}
      </header>
      {children}
    </div>
  );
}
