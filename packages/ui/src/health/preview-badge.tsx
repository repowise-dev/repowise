"use client";

import { createContext, useContext } from "react";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "../ui/tooltip";

export interface PreviewBadgeStrings {
  /** The badge text. */
  label: string;
  /** The accessible name, which carries the note so it is read without the tooltip. */
  ariaLabel: (note: string) => string;
}

const ENGLISH: PreviewBadgeStrings = {
  label: "Preview",
  ariaLabel: (note) => `Preview: ${note}`,
};

/**
 * The badge's copy for a host that translates. Provided once at the app root,
 * so the many views that render the badge need no strings prop of their own.
 */
export const PreviewBadgeStringsContext = createContext<PreviewBadgeStrings>(ENGLISH);

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
  label,
}: {
  note?: string | null | undefined;
  /** Overrides the provided label; its accessible name becomes "<label>: <note>". */
  label?: string;
}) {
  const strings = useContext(PreviewBadgeStringsContext);
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
            aria-label={label ? `${label}: ${text}` : strings.ariaLabel(text)}
            onClick={(e) => e.stopPropagation()}
            className="rounded border border-[var(--color-border-default)] px-1 font-mono text-[10px] uppercase tracking-[0.12em] text-[var(--color-text-secondary)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent-primary)]"
          >
            {label ?? strings.label}
          </button>
        </TooltipTrigger>
        <TooltipContent side="top" className="max-w-[280px] whitespace-normal">
          {text}
        </TooltipContent>
      </Tooltip>
    </TooltipProvider>
  );
}
