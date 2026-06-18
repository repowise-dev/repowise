"use client";

import * as React from "react";
import { cn } from "../lib/cn";

export interface CollapsibleSectionProps {
  title: React.ReactNode;
  /** Right-aligned hint shown on the toggle (e.g. a count). */
  hint?: React.ReactNode;
  defaultOpen?: boolean;
  children?: React.ReactNode;
  className?: string;
}

/**
 * The progressive-disclosure idiom used across Code Health: a borderless
 * toggle button that reveals a demoted table/queue beneath. One home so the
 * disclosure chrome restyles everywhere on a package bump.
 */
export function CollapsibleSection({
  title,
  hint,
  defaultOpen = false,
  children,
  className,
}: CollapsibleSectionProps) {
  const [open, setOpen] = React.useState(defaultOpen);
  return (
    <section className={cn("space-y-3", className)}>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        className="flex w-full items-center gap-2 rounded-lg border border-[var(--color-border-default)] bg-[var(--color-bg-surface)] px-3 py-2 text-sm font-medium text-[var(--color-text-secondary)] hover:text-[var(--color-text-primary)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent-primary)]"
      >
        <span className="text-[var(--color-text-tertiary)]">{open ? "▾" : "▸"}</span>
        {title}
        {hint != null && (
          <span className="ml-auto text-xs font-normal text-[var(--color-text-tertiary)]">
            {hint}
          </span>
        )}
      </button>
      {open ? <div className="space-y-3">{children}</div> : null}
    </section>
  );
}
