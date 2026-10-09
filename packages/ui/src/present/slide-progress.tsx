"use client";

import { cn } from "../lib/cn";

/**
 * Bottom-of-deck progress: one clickable dot per slide (a deck is at most a
 * dozen slides) and an "n / total" counter. Accent marks the current slide;
 * everything else stays neutral.
 */
export function SlideProgress({
  index,
  total,
  title,
  onSelect,
}: {
  index: number;
  total: number;
  /** Announced with the position, so a screen reader hears what the slide is. */
  title: string;
  onSelect: (i: number) => void;
}) {
  return (
    <div className="flex items-center gap-3">
      <div className="hidden items-center gap-1.5 sm:flex">
        {Array.from({ length: total }).map((_, i) => (
          <button
            key={i}
            type="button"
            onClick={() => onSelect(i)}
            aria-label={`Go to slide ${i + 1}`}
            aria-current={i === index ? "step" : undefined}
            className={cn(
              "h-1.5 rounded-full transition-all motion-reduce:transition-none focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent-primary)]",
              i === index
                ? "w-5 bg-[var(--color-accent-primary)]"
                : "w-1.5 bg-[var(--color-border-active)] hover:bg-[var(--color-text-tertiary)]",
            )}
          />
        ))}
      </div>
      <span aria-hidden className="font-mono text-[12px] tabular-nums text-[var(--color-text-tertiary)]">
        {index + 1} / {total}
      </span>
      <span aria-live="polite" className="sr-only">
        Slide {index + 1} of {total}: {title}
      </span>
    </div>
  );
}
