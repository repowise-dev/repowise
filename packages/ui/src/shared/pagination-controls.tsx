"use client";

export interface PaginationControlsProps {
  offset: number;
  shown: number;
  total: number;
  onPrevious?: (() => void) | undefined;
  onNext?: (() => void) | undefined;
  label?: string;
}

/** Shared bounded-list footer. Totals always describe the server-side set. */
export function PaginationControls({
  offset,
  shown,
  total,
  onPrevious,
  onNext,
  label = "items",
}: PaginationControlsProps) {
  if (total === 0) return null;
  const first = offset + 1;
  const last = offset + shown;
  return (
    <nav
      aria-label={`${label} pagination`}
      className="flex flex-col gap-3 border-t border-[var(--color-border-default)] pt-4 sm:flex-row sm:items-center sm:justify-between"
    >
      <p className="font-mono text-[10px] uppercase tracking-[0.12em] text-[var(--color-text-tertiary)]">
        Showing{" "}
        <span className="tabular-nums">
          {first.toLocaleString()}–{last.toLocaleString()}
        </span>{" "}
        of <span className="tabular-nums">{total.toLocaleString()}</span> {label}
      </p>
      <div className="flex gap-2">
        <button
          type="button"
          onClick={onPrevious}
          disabled={!onPrevious}
          className="rounded-lg border border-[var(--color-border-default)] px-3 py-1.5 text-xs font-medium text-[var(--color-text-secondary)] hover:border-[var(--color-border-hover)] hover:text-[var(--color-text-primary)] disabled:cursor-not-allowed disabled:opacity-40"
        >
          Previous
        </button>
        <button
          type="button"
          onClick={onNext}
          disabled={!onNext}
          className="rounded-lg border border-[var(--color-border-default)] px-3 py-1.5 text-xs font-medium text-[var(--color-text-secondary)] hover:border-[var(--color-border-hover)] hover:text-[var(--color-text-primary)] disabled:cursor-not-allowed disabled:opacity-40"
        >
          Next
        </button>
      </div>
    </nav>
  );
}
