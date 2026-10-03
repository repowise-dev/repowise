"use client";

import * as ProgressPrimitive from "@radix-ui/react-progress";
import { cn } from "../lib/cn";

export function Progress({
  value,
  className,
  indicatorClassName,
}: {
  value?: number;
  className?: string;
  indicatorClassName?: string;
}) {
  const boundedValue = value != null && Number.isFinite(value)
    ? Math.max(0, Math.min(100, value))
    : undefined;

  return (
    <ProgressPrimitive.Root
      value={boundedValue}
      className={cn(
        "relative h-2 w-full overflow-hidden rounded-full bg-[var(--color-bg-elevated)]",
        className,
      )}
    >
      <ProgressPrimitive.Indicator
        className={cn(
          "h-full w-full flex-1 bg-[var(--color-accent-primary)] transition-all duration-300",
          indicatorClassName,
        )}
        style={{ transform: `translateX(${(boundedValue ?? 0) - 100}%)` }}
      />
    </ProgressPrimitive.Root>
  );
}
