"use client";

import * as React from "react";
import { cn } from "@/lib/utils/cn";
import {
  scoreToStatus,
  statusBadgeClasses,
  statusLabel,
  type FreshnessStatus,
} from "@repowise/ui/lib/confidence";
import { formatConfidence } from "@repowise/ui/lib/format";
import { Tooltip, TooltipContent, TooltipTrigger } from "@repowise/ui/ui/tooltip";

interface ConfidenceBadgeProps {
  score: number;
  status?: string;
  showScore?: boolean;
  staleSince?: string | null;
  className?: string;
}

export function ConfidenceBadge({
  score,
  status: statusProp,
  showScore = false,
  staleSince,
  className,
}: ConfidenceBadgeProps) {
  const status = (statusProp as FreshnessStatus | undefined) ?? scoreToStatus(score);
  const badgeClasses = statusBadgeClasses(status);
  const label = statusLabel(status);

  const badge = (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded border px-2 py-0.5 text-xs font-medium transition-colors",
        badgeClasses,
        className,
      )}
    >
      <span
        className={cn(
          "h-1.5 w-1.5 rounded-full",
          status === "fresh" && "bg-green-500",
          status === "stale" && "animate-pulse bg-yellow-500",
          status === "outdated" && "bg-red-500",
        )}
      />
      {label}
      {showScore && <span className="opacity-70">· {formatConfidence(score)}</span>}
    </span>
  );

  if (status === "stale" && staleSince) {
    return (
      <Tooltip>
        <TooltipTrigger asChild>{badge}</TooltipTrigger>
        <TooltipContent>
          <p>Stale since {new Date(staleSince).toLocaleDateString()}</p>
          <p className="text-[var(--color-text-tertiary)]">
            Confidence: {formatConfidence(score)}
          </p>
        </TooltipContent>
      </Tooltip>
    );
  }

  return badge;
}
