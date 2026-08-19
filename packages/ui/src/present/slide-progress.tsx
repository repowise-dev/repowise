"use client";

import { cn } from "../lib/cn";

/**
 * Bottom-of-deck progress: clickable dots when the deck is short, a slim bar
 * when it's long, always with a "n / total" counter. Accent marks the current
 * slide; everything else stays neutral (Linear-style restraint).
 */
export function SlideProgress({
  index,
  total,
  onSelect,
}: {
  index: number;
  total: number;
  onSelect: (i: number) => void;
}) {
  const showDots = total <= 16;
  return (
    <div className="flex items-center gap-3">
      {showDots ? (
        <div className="flex items-center gap-1.5">
          {Array.from({ length: total }).map((_, i) => (
            <button
              key={i}
              type="button"
              onClick={() => onSelect(i)}
              aria-label={`Go to slide ${i + 1}`}
              aria-current={i === index ? "true" : undefined}
              className={cn(
                "h-1.5 rounded-full transition-all",
                i === index
                  ? "w-5 bg-[var(--color-accent-primary)]"
                  : "w-1.5 bg-[var(--color-border-active)] hover:bg-[var(--color-text-tertiary)]",
              )}
            />
          ))}
        </div>
      ) : (
        <div className="h-1.5 w-40 overflow-hidden rounded-full bg-[var(--color-border-active)]">
          <div
            className="h-full rounded-full bg-[var(--color-accent-primary)] transition-all"
            style={{ width: `${((index + 1) / total) * 100}%` }}
          />
        </div>
      )}
      <span className="text-xs tabular-nums text-[var(--color-text-tertiary)]">
        {index + 1} / {total}
      </span>
    </div>
  );
}
