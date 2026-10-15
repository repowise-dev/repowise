"use client";

import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "../ui/tooltip";

/**
 * The mark a row carries when its language cell is served as a preview.
 *
 * A preview cell's findings are shown by default but have not cleared the
 * measured bar a validated cell has. The server sends the measurement as one
 * line ("preview, measured 22% precision on 55 judged plans ..."); the tooltip
 * shows it so a reader can weigh the row. Renders nothing without a note.
 */
export function PreviewBadge({
  note,
  label = "Preview",
}: {
  note?: string | null | undefined;
  label?: string;
}) {
  const text = note?.trim();
  if (!text) return null;
  return (
    <TooltipProvider delayDuration={150}>
      <Tooltip>
        <TooltipTrigger asChild>
          {/* A button so the note is reachable by keyboard; the click is
              swallowed so a clickable row underneath does not activate. */}
          <button
            type="button"
            data-preview
            aria-label={`${label}: ${text}`}
            onClick={(e) => e.stopPropagation()}
            className="rounded border border-[var(--color-border-default)] px-1 font-mono text-[10px] uppercase tracking-[0.12em] text-[var(--color-text-secondary)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent-primary)]"
          >
            {label}
          </button>
        </TooltipTrigger>
        <TooltipContent side="top" className="max-w-[280px] whitespace-normal">
          {text}
        </TooltipContent>
      </Tooltip>
    </TooltipProvider>
  );
}
