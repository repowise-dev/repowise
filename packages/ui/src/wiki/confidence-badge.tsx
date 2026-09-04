"use client";

import * as React from "react";
import { cn } from "../lib/cn";
import {
  scoreToStatus,
  statusBadgeClasses,
  statusLabel,
  type FreshnessStatus,
} from "../lib/confidence";
import { formatConfidence, formatDate } from "../lib/format";
import { Tooltip, TooltipContent, TooltipTrigger } from "../ui/tooltip";
import { ActivityDot } from "../ui/activity-dot";

interface ConfidenceBadgeProps {
  score: number;
  status?: string;
  showScore?: boolean;
  staleSince?: string | null;
  className?: string;
  /**
   * Drop to the status dot alone below `sm`.
   *
   * For crowded chrome rows on a phone. The dot already carries the state —
   * colour is the whole signal, and the word beside it is the widest thing in
   * the row. Opt-in, because in a table or a card the label is the readable
   * part and losing it would be a downgrade.
   */
  compact?: boolean;
}

export function ConfidenceBadge({
  score,
  status: statusProp,
  showScore = false,
  staleSince,
  className,
  compact = false,
}: ConfidenceBadgeProps) {
  const status = (statusProp as FreshnessStatus | undefined) ?? scoreToStatus(score);
  const badgeClasses = statusBadgeClasses(status);
  const label = statusLabel(status);

  const badge = (
    <span
      {...(compact ? { title: label } : {})}
      className={cn(
        "inline-flex shrink-0 items-center gap-1.5 rounded border py-0.5 text-xs font-medium transition-colors",
        compact ? "px-1.5 sm:px-2" : "px-2",
        badgeClasses,
        className,
      )}
    >
      {status === "stale" ? (
        <ActivityDot className="h-1.5 w-1.5 bg-[var(--color-warning)]" />
      ) : (
        <span
          className={cn(
            "h-1.5 w-1.5 shrink-0 rounded-full",
            status === "fresh" && "bg-[var(--color-success)]",
            status === "outdated" && "bg-[var(--color-error)]",
          )}
        />
      )}
      {/* Hidden visually, never from a screen reader: the colour is the signal
          on a phone, but "Fresh" is still the name of the state. */}
      <span className={cn(compact && "sr-only sm:not-sr-only")}>{label}</span>
      {showScore && (
        <span className={cn("opacity-70", compact && "hidden sm:inline")}>
          · {formatConfidence(score)}
        </span>
      )}
    </span>
  );

  if (status === "stale" && staleSince) {
    return (
      <Tooltip>
        <TooltipTrigger asChild>{badge}</TooltipTrigger>
        <TooltipContent>
          <p>Stale since {formatDate(staleSince)}</p>
          <p className="text-[var(--color-text-tertiary)]">
            Confidence: {formatConfidence(score)}
          </p>
        </TooltipContent>
      </Tooltip>
    );
  }

  return badge;
}
