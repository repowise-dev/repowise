import type { ReactNode } from "react";

import { Skeleton, SkeletonRegion } from "../ui/skeleton";
import { MetricCard } from "./metric-card";
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

export interface StatGridSkeletonProps {
  /** Number of tiles. Match the count the loaded grid will render. */
  count?: number;
  /**
   * Tailwind grid-column classes. Must match the real grid's, or the tiles
   * reflow into a different number of rows when data lands.
   */
  columns?: string;
  /** Set true where the real tiles carry a one-line description. */
  description?: boolean;
  className?: string;
}

/**
 * A row of stat tiles, waiting. Each tile is a real `MetricCard` with bars in
 * place of its label and value, so the height is the card's own height rather
 * than a guess. The hand-rolled version of this was `h-24` (96px) against a
 * card that measures 80-88px, so the grid snapped upward on arrival.
 */
export function StatGridSkeleton({
  count = 3,
  columns = "grid-cols-1 sm:grid-cols-3",
  description = false,
  className,
}: StatGridSkeletonProps) {
  return (
    <SkeletonRegion
      className={cn("grid gap-3", columns, className)}
      label="Loading metrics"
    >
      {Array.from({ length: count }).map((_, i) => (
        <MetricCard
          key={i}
          label={<Skeleton className="block h-[1lh] w-24" />}
          value={<Skeleton className="block h-[1lh] w-16" />}
          {...(description
            ? { description: <Skeleton className="block h-[1lh] w-32" /> }
            : {})}
        />
      ))}
    </SkeletonRegion>
  );
}

export interface ChartSkeletonProps {
  /**
   * Pass the SAME height the chart is given. Import the chart's own constant
   * rather than retyping a number, so the two cannot drift apart.
   */
  height: number;
  className?: string;
  label?: string;
}

/**
 * A plot area, waiting, at exactly the height the chart will occupy. Takes
 * `height` as a required prop for that reason: a default would invite call
 * sites to omit it and silently reserve the wrong box.
 */
export function ChartSkeleton({
  height,
  className,
  label = "Loading chart",
}: ChartSkeletonProps) {
  return (
    <SkeletonRegion className={className} label={label}>
      <Skeleton className="w-full" style={{ height }} />
    </SkeletonRegion>
  );
}
