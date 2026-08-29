import type { ReactNode } from "react";

import { Skeleton, SkeletonRegion } from "../ui/skeleton";
import { cn } from "../lib/cn";
import {
  PAGE_SHELL_CONTAINER,
  PAGE_SHELL_DESCRIPTION,
  PAGE_SHELL_HEADER,
  PAGE_SHELL_MAX_WIDTH,
  PAGE_SHELL_TITLE,
} from "./page-shell";

export interface TableSkeletonProps {
  /** Number of placeholder rows. Default 6. */
  rows?: number;
  className?: string;
}

/** Loading placeholder shaped like a table: a stack of row-height bars. */
export function TableSkeleton({ rows = 6, className }: TableSkeletonProps) {
  return (
    <SkeletonRegion className={cn("space-y-2", className)} label="Loading rows">
      {Array.from({ length: rows }).map((_, i) => (
        <Skeleton key={i} className="h-10 w-full" />
      ))}
    </SkeletonRegion>
  );
}

export interface CardSkeletonProps {
  /** Number of placeholder text lines under the title bar. Default 3. */
  lines?: number;
  className?: string;
}

/** Loading placeholder shaped like a card: a title bar over shorter lines. */
export function CardSkeleton({ lines = 3, className }: CardSkeletonProps) {
  return (
    <SkeletonRegion
      className={cn(
        "rounded-lg border border-[var(--color-border-default)] bg-[var(--color-bg-surface)] p-4 space-y-3",
        className,
      )}
    >
      <Skeleton className="h-4 w-1/3" />
      <div className="space-y-2">
        {Array.from({ length: lines }).map((_, i) => (
          <Skeleton key={i} className={cn("h-3", i === lines - 1 ? "w-2/3" : "w-full")} />
        ))}
      </div>
    </SkeletonRegion>
  );
}

export interface PageSkeletonProps {
  /** Must match the `maxWidth` the real page passes to `PageShell`. */
  maxWidth?: keyof typeof PAGE_SHELL_MAX_WIDTH;
  /** Set false for a page whose `PageShell` has no `description`. */
  description?: boolean;
  /** Set false for a page whose `PageShell` has no `actions`. */
  actions?: boolean;
  label?: string;
  /** Body shapes. Omit to reserve nothing below the header. */
  children?: ReactNode;
}

/**
 * The `PageShell` frame, waiting. Each bar sits inside the real heading or
 * description element and is sized `1lh`, so it reserves that element's own
 * line box rather than a guessed pixel height. Change the type scale and the
 * placeholder follows; there is no literal to fall out of sync.
 *
 * The header is exact. The body is whatever you pass: give it shapes only
 * where you actually know the layout, because a guessed body reflows on
 * arrival, which reads slower than having reserved nothing.
 */
export function PageSkeleton({
  maxWidth = "default",
  description = true,
  actions = true,
  label = "Loading page",
  children,
}: PageSkeletonProps) {
  return (
    <SkeletonRegion
      className={cn(PAGE_SHELL_CONTAINER, PAGE_SHELL_MAX_WIDTH[maxWidth])}
      label={label}
    >
      <header className={PAGE_SHELL_HEADER}>
        <div className="min-w-0 flex-1 space-y-1">
          <h1 className={PAGE_SHELL_TITLE}>
            <Skeleton className="h-[1lh] w-52 max-w-full" />
          </h1>
          {description && (
            <p className={PAGE_SHELL_DESCRIPTION}>
              <Skeleton className="block h-[1lh] w-full" />
            </p>
          )}
        </div>
        {actions && <Skeleton className="h-8 w-24 shrink-0" />}
      </header>
      {children}
    </SkeletonRegion>
  );
}
