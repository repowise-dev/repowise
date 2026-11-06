import * as React from "react";
import { cn } from "../lib/cn";
import { EmptyState, type EmptyStateAction } from "./empty-state";

/** Copy a host can override on a view's clean state; omitted keys keep the view's English. */
export interface CleanStateCopy {
  title?: string;
  description?: string;
}

export interface CleanStateProps {
  /** Names the result with its scope: "No dead code across 5,612 files". */
  title: string;
  /** One plain sentence on what was checked, or what keeps it this way. */
  description?: React.ReactNode;
  action?: EmptyStateAction;
  secondaryAction?: EmptyStateAction;
  /** `start` when it sits under a lede rather than standing alone. Default `center`. */
  align?: "center" | "start";
  /** Default `h3`. */
  titleAs?: "h2" | "h3";
  className?: string;
  children?: React.ReactNode;
}

/**
 * The good result of a check: it ran, and found nothing to fix.
 *
 * Calm on purpose: the success check and plain words that name the scope the
 * claim covers, with no animation or fill. Render it only when the check is
 * known to have run; "never ran" is a neutral empty state, not good news.
 */
export function CleanState({
  title,
  description,
  action,
  secondaryAction,
  align = "center",
  titleAs,
  className,
  children,
}: CleanStateProps) {
  return (
    <EmptyState
      tone="positive"
      title={title}
      description={description}
      {...(action ? { action } : {})}
      {...(secondaryAction ? { secondaryAction } : {})}
      {...(titleAs ? { titleAs } : {})}
      className={cn(align === "start" && "items-start px-0 py-0 text-left", className)}
    >
      {children}
    </EmptyState>
  );
}
