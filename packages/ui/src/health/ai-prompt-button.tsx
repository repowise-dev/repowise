"use client";

import { Sparkles } from "lucide-react";
import { cn } from "../lib/cn";

export interface AiPromptButtonProps {
  onClick: () => void;
  /** Visible label for the default variant; also used as the title/aria-label. */
  label?: string;
  /**
   * `default` shows the green sparkles pill with a label (cards, headers).
   * `icon` is a compact icon-only button for dense table rows; the label
   * still rides along as the tooltip / accessible name.
   */
  variant?: "default" | "icon";
  className?: string;
}

/**
 * Canonical "hand this to an AI agent" affordance. One button, used everywhere
 * an AI prompt can be generated (refactor, coverage, dead code, security,
 * hotspots) so the action reads the same on every screen. The host owns the
 * click — typically opening the shared AiPromptModal.
 */
export function AiPromptButton({
  onClick,
  label = "AI fix prompt",
  variant = "default",
  className,
}: AiPromptButtonProps) {
  const base =
    "group/ai inline-flex items-center gap-1.5 rounded-md border border-[var(--color-success)]/40 bg-[var(--color-success)]/10 font-semibold text-[var(--color-success)] transition-colors hover:bg-[var(--color-success)]/20 hover:border-[var(--color-success)]/60";
  if (variant === "icon") {
    return (
      <button
        type="button"
        onClick={(e) => {
          e.stopPropagation();
          onClick();
        }}
        title={label}
        aria-label={label}
        className={cn(base, "h-6 w-6 justify-center p-0", className)}
      >
        <Sparkles className="h-3.5 w-3.5 transition-transform group-hover/ai:rotate-12" />
      </button>
    );
  }
  return (
    <button
      type="button"
      onClick={(e) => {
        e.stopPropagation();
        onClick();
      }}
      title={label}
      className={cn(base, "px-2.5 py-1 text-xs", className)}
    >
      <Sparkles className="h-3.5 w-3.5 transition-transform group-hover/ai:rotate-12" />
      {label}
    </button>
  );
}
