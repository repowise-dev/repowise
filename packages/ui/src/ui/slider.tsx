"use client";

import * as SliderPrimitive from "@radix-ui/react-slider";
import { cn } from "../lib/cn";

/**
 * Radix puts `role="slider"` on the Thumb, so a naming prop spread onto the
 * Root labels an element assistive tech never announces and leaves the actual
 * control anonymous. Both naming props are forwarded to the Thumb instead.
 * (`<label htmlFor>` reaches neither: they are spans, not labelable elements.)
 */
export function Slider({
  className,
  "aria-label": ariaLabel,
  "aria-labelledby": ariaLabelledBy,
  ...props
}: React.ComponentPropsWithoutRef<typeof SliderPrimitive.Root>) {
  return (
    <SliderPrimitive.Root
      className={cn(
        "relative flex w-full touch-none select-none items-center",
        className,
      )}
      {...props}
    >
      <SliderPrimitive.Track className="relative h-1.5 w-full grow overflow-hidden rounded-full bg-[var(--color-bg-elevated)]">
        <SliderPrimitive.Range className="absolute h-full bg-[var(--color-accent-primary)]" />
      </SliderPrimitive.Track>
      <SliderPrimitive.Thumb
        aria-label={ariaLabel}
        aria-labelledby={ariaLabelledBy}
        className={cn(
          "block h-4 w-4 rounded-full border border-[var(--color-accent-primary)] bg-[var(--color-bg-surface)] shadow",
          "transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent-primary)]",
          "disabled:pointer-events-none disabled:opacity-50",
        )}
      />
    </SliderPrimitive.Root>
  );
}
