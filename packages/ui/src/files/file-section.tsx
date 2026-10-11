import * as React from "react";
import { cn } from "../lib/cn";

export interface FileSectionProps {
  title: string;
  /** What the figures in it mean. Not the section's own name again. */
  description?: React.ReactNode;
  action?: React.ReactNode;
  /**
   * The first section inside a tab panel. The tab row already draws a hairline
   * directly above it, so a second one 12 units down is a rule where the
   * layout already has one.
   */
  first?: boolean;
  className?: string;
  children: React.ReactNode;
}

/**
 * The grouping device for everything inside a file tab: a hairline and vertical
 * rhythm, not a bordered box. Rule 1 — a card means "a discrete object you can
 * act on", and a table of function churn is not that.
 *
 * The measurements are `files-index.tsx`'s heavier variant (`mt-12` / `pt-8`,
 * `text-lg` heading) rather than `OverviewSection`'s, deliberately: the two
 * Files surfaces link into each other constantly and a reader moving between
 * them should not cross a type-scale change to do it.
 */
export function FileSection({
  title,
  description,
  action,
  first = false,
  className,
  children,
}: FileSectionProps) {
  return (
    <section
      className={cn(
        "space-y-3",
        !first && "mt-12 border-t border-[var(--color-border-default)] pt-8",
        className,
      )}
    >
      <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
        <h2 className="text-lg font-semibold text-[var(--color-text-primary)]">{title}</h2>
        {action}
      </div>
      {description && (
        <p className="max-w-[75ch] text-sm leading-relaxed text-[var(--color-text-secondary)] [text-wrap:pretty]">
          {description}
        </p>
      )}
      {children}
    </section>
  );
}

/** A figure inside a section's sentence. Matches `files-index`'s `Fig` so the
 *  two surfaces emphasise numbers the same way. No `tabular-nums`: it is not in
 *  a column and cannot change in place, which is where that rule applies. */
export function Fig({ children }: { children: React.ReactNode }) {
  return <span className="font-medium text-[var(--color-text-primary)]">{children}</span>;
}
