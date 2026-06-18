import * as React from "react";
import { cn } from "../lib/cn";

export interface PageShellProps {
  title: string;
  icon?: React.ReactNode;
  description?: string;
  /** Right-aligned action slot in the header band. */
  actions?: React.ReactNode;
  /** `default` ~1280px for reading surfaces; `wide` ~1600px for canvases. */
  maxWidth?: "default" | "wide";
  className?: string;
  children?: React.ReactNode;
}

/**
 * The single page frame: outer padding, centred max width, vertical rhythm,
 * and one header band (title + optional icon + one-line description + actions).
 * Replaces the hand-rolled per-page headers.
 */
export function PageShell({
  title,
  icon,
  description,
  actions,
  maxWidth = "default",
  className,
  children,
}: PageShellProps) {
  return (
    <div
      className={cn(
        "mx-auto w-full p-[var(--page-pad)] space-y-[var(--section-gap)]",
        maxWidth === "wide" ? "max-w-[1600px]" : "max-w-[1280px]",
        className,
      )}
    >
      <header className="flex flex-wrap items-start justify-between gap-4">
        <div className="min-w-0 space-y-1">
          <h1 className="flex items-center gap-2 text-xl font-semibold text-[var(--color-text-primary)]">
            {icon}
            {title}
          </h1>
          {description && (
            <p className="text-sm text-[var(--color-text-secondary)]">{description}</p>
          )}
        </div>
        {actions && <div className="flex shrink-0 items-center gap-2">{actions}</div>}
      </header>
      {children}
    </div>
  );
}
