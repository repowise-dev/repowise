"use client";

import { Cog, Sparkles } from "lucide-react";
import { cn } from "../lib/cn";

/** How a repo's wiki stands, from the repos API `docs_mode`. `deterministic`
 *  means the subsystem pages are still structural stubs; it flips to `llm` once
 *  a model has written every one of them. */
export type DocsMode = "none" | "deterministic" | "llm";

const TITLE =
  "The subsystem pages are still generated from code structure. Write them with a model for the how and why.";

const CHIP = cn(
  "inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[10px] font-medium uppercase tracking-wider",
  "border-[var(--color-info)]/35 bg-[var(--color-info)]/10 text-[var(--color-info)]",
);

/** A small chip for a repo header, shown only while the subsystem pages are
 *  unwritten. A fully written wiki (`llm`) and an undocumented repo (`none`)
 *  both render nothing: a badge every repo carries says nothing, so the header
 *  stays quiet unless there is prose still to write. With `onAction` the chip
 *  becomes a button that offers a model (`actionLabel`). */
export function DocsModeBadge({
  mode,
  className,
  onAction,
  label = "Subsystem pages unwritten",
  actionLabel = "Write with a model",
  title = TITLE,
}: {
  mode: DocsMode;
  className?: string;
  onAction?: () => void;
  label?: string;
  actionLabel?: string;
  /** Hover text. */
  title?: string;
}) {
  if (mode !== "deterministic") return null;
  if (!onAction) {
    return (
      <span title={title} className={cn(CHIP, className)}>
        <Cog className="h-2.5 w-2.5" />
        {label}
      </span>
    );
  }
  return (
    <button
      type="button"
      title={title}
      onClick={onAction}
      className={cn(
        "group inline-flex items-center gap-2 rounded-full focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-model)]",
        className,
      )}
    >
      {/* Phones keep only the action: the chip's wording is in the title. */}
      <span className={cn(CHIP, "hidden sm:inline-flex")}>
        <Cog className="h-2.5 w-2.5" />
        {label}
      </span>
      <span className="inline-flex items-center gap-1 text-xs font-medium text-[var(--color-model)] group-hover:underline">
        <Sparkles aria-hidden className="h-3 w-3" />
        {actionLabel}
      </span>
    </button>
  );
}
