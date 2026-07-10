import { Skeleton } from "../ui/skeleton";
import { cn } from "../lib/cn";

export interface TableSkeletonProps {
  /** Number of placeholder rows. Default 6. */
  rows?: number;
  className?: string;
}

/** Loading placeholder shaped like a table: a stack of row-height bars. */
export function TableSkeleton({ rows = 6, className }: TableSkeletonProps) {
  return (
    <div className={cn("space-y-2", className)} aria-hidden>
      {Array.from({ length: rows }).map((_, i) => (
        <Skeleton key={i} className="h-10 w-full" />
      ))}
    </div>
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
    <div
      className={cn(
        "rounded-lg border border-[var(--color-border-default)] bg-[var(--color-bg-surface)] p-4 space-y-3",
        className,
      )}
      aria-hidden
    >
      <Skeleton className="h-4 w-1/3" />
      <div className="space-y-2">
        {Array.from({ length: lines }).map((_, i) => (
          <Skeleton key={i} className={cn("h-3", i === lines - 1 ? "w-2/3" : "w-full")} />
        ))}
      </div>
    </div>
  );
}
