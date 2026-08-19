import * as React from "react";
import { cn } from "../lib/cn";
import { Button } from "../ui/button";

interface EmptyStateProps {
  icon?: React.ReactNode;
  title: string;
  description?: string;
  action?: {
    label: string;
    onClick: () => void;
  };
  className?: string;
  /**
   * Heading level for the title. Defaults to `h3`, which is right where the
   * empty state stands in for a section inside one. Pass `h2` where it *is*
   * the whole panel — the file page's tab bodies do, and without it the page
   * runs `h1` (the path) straight to `h3` with nothing between.
   */
  titleAs?: "h2" | "h3";
}

export function EmptyState({
  icon,
  title,
  description,
  action,
  className,
  titleAs: Heading = "h3",
}: EmptyStateProps) {
  return (
    <div
      className={cn(
        "flex flex-col items-center justify-center gap-4 rounded-lg border border-dashed border-[var(--color-border-default)] p-12 text-center [background-image:var(--gradient-warm-wash)]",
        className,
      )}
    >
      {icon && (
        <div className="flex h-14 w-14 items-center justify-center rounded-2xl text-[var(--color-text-on-accent)] shadow-[var(--shadow-md)] [background-image:var(--gradient-warm)]">
          {icon}
        </div>
      )}
      <div className="space-y-1">
        <Heading className="text-sm font-semibold text-[var(--color-text-primary)]">
          {title}
        </Heading>
        {description && (
          <p className="text-sm text-[var(--color-text-secondary)]">{description}</p>
        )}
      </div>
      {action && (
        <Button size="sm" onClick={action.onClick}>
          {action.label}
        </Button>
      )}
    </div>
  );
}
