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
  narrow: "max-w-3xl",
  default: "max-w-[1280px]",
  wide: "max-w-[1600px]",
} as const;
export const PAGE_SHELL_HEADER =
  "flex flex-wrap items-start justify-between gap-4";
/** The h1's type styles (22px on the product scale), so a placeholder
 *  inherits the real line box. */
export const PAGE_SHELL_TITLE =
  "flex items-center gap-2 text-[22px] leading-[1.3] font-semibold text-[var(--color-text-primary)]";
/** Title icon slot: one neutral tint and size for every page. */
const PAGE_SHELL_ICON =
  "inline-flex shrink-0 text-[var(--color-text-tertiary)] [&_svg]:h-5 [&_svg]:w-5";
/** The description's type styles, including the readable-measure cap. */
export const PAGE_SHELL_DESCRIPTION =
  "max-w-[68ch] text-sm text-[var(--color-text-secondary)]";

export type PageMaxWidth = keyof typeof PAGE_SHELL_MAX_WIDTH;

export interface PageShellProps {
  title: string;
  /**
   * Bare icon (no colour class). The shell sizes it to 20px and tints it
   * tertiary so every page title reads the same; a colour class on the icon
   * itself would override that, so leave it off.
   */
  icon?: React.ReactNode;
  description?: string;
  /** Right-aligned action slot in the header band. */
  actions?: React.ReactNode;
  /**
   * `narrow` ~768px for forms and settings; `default` ~1280px for scan and
   * reading surfaces; `wide` ~1600px for canvases.
   */
  maxWidth?: PageMaxWidth;
  className?: string;
  children?: React.ReactNode;
}

export interface PageFrameProps {
  /** Same widths as `PageShell`. */
  maxWidth?: PageMaxWidth;
  className?: string | undefined;
  children?: React.ReactNode;
}

/**
 * `PageShell`'s container without its header: same padding, centred width and
 * section rhythm, for pages that draw their own header (a file path, a
 * canvas toolbar). Use it instead of hand-setting `max-w-*` and padding.
 */
export function PageFrame({ maxWidth = "default", className, children }: PageFrameProps) {
  return (
    <div className={cn(PAGE_SHELL_CONTAINER, PAGE_SHELL_MAX_WIDTH[maxWidth], className)}>
      {children}
    </div>
  );
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
    <PageFrame maxWidth={maxWidth} className={className}>
      <header className={PAGE_SHELL_HEADER}>
        <div className="min-w-0 space-y-1">
          <h1 className={PAGE_SHELL_TITLE}>
            {icon && (
              <span aria-hidden className={PAGE_SHELL_ICON}>
                {icon}
              </span>
            )}
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
        {/* Wraps rather than refusing to shrink. `shrink-0` here sized the
            block to its contents and let it run off the side of a phone,
            taking the whole page's horizontal scroll with it. */}
        {actions && (
          <div className="flex min-w-0 flex-wrap items-center justify-end gap-2">
            {actions}
          </div>
        )}
      </header>
      {children}
    </PageFrame>
  );
}
