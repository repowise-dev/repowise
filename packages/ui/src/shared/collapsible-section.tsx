"use client";

import * as React from "react";
import { ChevronRight } from "lucide-react";
import { cn } from "../lib/cn";

export interface CollapsibleSectionProps {
  title: React.ReactNode;
  /** Right-aligned hint shown on the toggle (e.g. a count). */
  hint?: React.ReactNode;
  defaultOpen?: boolean;
  children?: React.ReactNode;
  className?: string;
  /**
   * `hairline` (default) is a rule above a borderless toggle, the section
   * look. `boxed` keeps the older bordered button for a surface that needs
   * the toggle to read as a control on its own.
   */
  variant?: "hairline" | "boxed";
  /** Tighter row and a 12px title, for a disclosure nested inside another. */
  dense?: boolean;
}

/**
 * The progressive-disclosure idiom used across the product: a toggle that
 * reveals a demoted table, queue or audit trail beneath. One home so the
 * disclosure chrome restyles everywhere on a package bump.
 *
 * The body mounts only while open, so a closed section costs nothing to
 * render and a drawer with six of them opens as fast as one with none.
 */
export function CollapsibleSection({
  title,
  hint,
  defaultOpen = false,
  children,
  className,
  variant = "hairline",
  dense = false,
}: CollapsibleSectionProps) {
  const [open, setOpen] = React.useState(defaultOpen);
  const bodyId = React.useId();

  if (variant === "boxed") {
    return (
      <section className={cn("space-y-3", className)}>
        <button
          type="button"
          onClick={() => setOpen((v) => !v)}
          aria-expanded={open}
          aria-controls={bodyId}
          className="flex w-full items-center gap-2 rounded-lg border border-[var(--color-border-default)] bg-[var(--color-bg-surface)] px-3 py-2 text-sm font-medium text-[var(--color-text-secondary)] hover:text-[var(--color-text-primary)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-focus-ring)]"
        >
          <span aria-hidden="true" className="text-[var(--color-text-tertiary)]">
            {open ? "▾" : "▸"}
          </span>
          {title}
          {hint != null && (
            <span className="ml-auto text-xs font-normal text-[var(--color-text-tertiary)]">
              {hint}
            </span>
          )}
        </button>
        {open ? (
          <div id={bodyId} className="space-y-3">
            {children}
          </div>
        ) : null}
      </section>
    );
  }

  return (
    <section className={cn("border-t border-[var(--color-border-default)]", className)}>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        aria-controls={bodyId}
        className={cn(
          "flex w-full min-w-0 items-center gap-2 rounded-sm text-left font-medium text-[var(--color-text-secondary)] transition-colors hover:text-[var(--color-text-primary)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-focus-ring)]",
          dense ? "py-2 text-xs" : "py-2.5 text-[15px]",
        )}
      >
        <ChevronRight
          aria-hidden="true"
          className={cn(
            "h-3.5 w-3.5 shrink-0 text-[var(--color-text-tertiary)] transition-transform motion-reduce:transition-none",
            open && "rotate-90",
          )}
        />
        <span className="min-w-0 flex-1">{title}</span>
        {hint != null && (
          <span className="ml-auto inline-flex shrink-0 items-center gap-2 text-xs font-normal tabular-nums text-[var(--color-text-tertiary)]">
            {hint}
          </span>
        )}
      </button>
      {open ? (
        <div id={bodyId} className={cn("flex flex-col gap-3", dense ? "pb-2" : "pb-4 pt-1")}>
          {children}
        </div>
      ) : null}
    </section>
  );
}
