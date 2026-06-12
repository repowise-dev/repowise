"use client";

import { Info } from "lucide-react";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "../ui/tooltip";
import { cn } from "../lib/cn";

export interface InfoTipProps {
  /** Tooltip body — plain text or rich content. */
  content: React.ReactNode;
  /** Optional accessible label; defaults to "More info". */
  label?: string;
  className?: string;
  side?: "top" | "right" | "bottom" | "left";
}

/**
 * A small inline info icon with a hover/focus tooltip. Click also toggles it,
 * so the explanation is reachable on touch devices where hover never fires.
 */
export function InfoTip({ content, label = "More info", className, side }: InfoTipProps) {
  return (
    <TooltipProvider delayDuration={150}>
      <Tooltip>
        <TooltipTrigger asChild>
          <button
            type="button"
            aria-label={label}
            onClick={(e) => e.stopPropagation()}
            className={cn(
              "inline-flex shrink-0 items-center text-[var(--color-text-tertiary)] hover:text-[var(--color-text-secondary)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent-primary)] rounded-sm",
              className,
            )}
          >
            <Info className="h-3 w-3" aria-hidden="true" />
          </button>
        </TooltipTrigger>
        <TooltipContent side={side ?? "top"} className="max-w-[280px] whitespace-normal">
          {content}
        </TooltipContent>
      </Tooltip>
    </TooltipProvider>
  );
}
