"use client";

import { Cog } from "lucide-react";
import { cn } from "../lib/cn";

/** How a repo's wiki stands, from the repos API `docs_mode`. `deterministic`
 *  means the subsystem pages are still structural stubs; it flips to `llm` once
 *  a model has written every one of them. */
export type DocsMode = "none" | "deterministic" | "llm";

/** A small chip for a repo header, shown only while the subsystem pages are
 *  unwritten. A fully written wiki (`llm`) and an undocumented repo (`none`)
 *  both render nothing: a badge every repo carries says nothing, so the header
 *  stays quiet unless there is prose still to write. */
export function DocsModeBadge({
  mode,
  className,
}: {
  mode: DocsMode;
  className?: string;
}) {
  if (mode !== "deterministic") return null;
  return (
    <span
      title="The subsystem pages are still generated from code structure. Write them with a model for the how and why."
      className={cn(
        "inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[10px] font-medium uppercase tracking-wider",
        "border-[var(--color-info)]/35 bg-[var(--color-info)]/10 text-[var(--color-info)]",
        className,
      )}
    >
      <Cog className="h-2.5 w-2.5" />
      Subsystem pages unwritten
    </span>
  );
}
