"use client";

import { Cog, Sparkles, Blend } from "lucide-react";
import { cn } from "../lib/cn";

/** How a repo's wiki was written. `deterministic` and `mixed` both come from
 *  the repos API `docs_mode: "deterministic"` (which only flips to `llm` once
 *  no template page remains); a caller that can count provenance passes
 *  `mixed` so a partly-upgraded repo never reads as fully written. */
export type DocsMode = "none" | "deterministic" | "mixed" | "llm";

const CONFIG: Record<
  Exclude<DocsMode, "none">,
  { label: string; title: string; icon: typeof Cog; className: string }
> = {
  deterministic: {
    label: "Auto docs",
    title:
      "Generated from code structure (no AI). Upgrade individual pages, or write them all, with a model.",
    icon: Cog,
    className:
      "border-[var(--color-info)]/35 bg-[var(--color-info)]/10 text-[var(--color-info)]",
  },
  mixed: {
    label: "Mixed docs",
    title:
      "Some pages are AI-written, the rest are generated from code structure. Write the remaining ones with a model.",
    icon: Blend,
    className:
      "border-[var(--color-accent-primary)]/35 bg-[var(--color-accent-muted)] text-[var(--color-accent-primary)]",
  },
  llm: {
    label: "AI docs",
    title: "Written by AI from the code and its context.",
    icon: Sparkles,
    className:
      "border-[var(--color-accent-primary)]/40 bg-[var(--color-accent-muted)] text-[var(--color-accent-primary)]",
  },
};

/** A small provenance chip for a repo header. Renders nothing when there are
 *  no docs yet (`none`), so the header stays quiet until a repo is documented. */
export function DocsModeBadge({
  mode,
  className,
}: {
  mode: DocsMode;
  className?: string;
}) {
  if (mode === "none") return null;
  const { label, title, icon: Icon, className: tone } = CONFIG[mode];
  return (
    <span
      title={title}
      className={cn(
        "inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[10px] font-medium uppercase tracking-wider",
        tone,
        className,
      )}
    >
      <Icon className="h-2.5 w-2.5" />
      {label}
    </span>
  );
}
