"use client";

import { Send } from "lucide-react";
import { cn } from "../lib/cn";
import type { ChatSuggestion } from "@repowise-dev/types/chat";

export interface ChatSuggestionsProps {
  suggestions: readonly ChatSuggestion[];
  onSelect: (suggestion: ChatSuggestion) => void;
  /** `chips` for the compact dock and for follow-ups beneath an answer;
   *  `rows` for the empty state, where the list is the page's subject. */
  layout: "chips" | "rows";
  /** Micro-label above a `rows` list. Chips carry no heading. */
  label?: string;
  /** Group name for a screen reader. Defaults to `label` where one shows. */
  ariaLabel?: string;
  className?: string;
}

/**
 * The one place a suggestion becomes a control, so a chip cannot drift into two
 * designs. A suggestion is a question, not an object acted on repeatedly, so
 * neither layout uses a card.
 */
export function ChatSuggestions({
  suggestions,
  onSelect,
  layout,
  label,
  ariaLabel,
  className,
}: ChatSuggestionsProps) {
  if (suggestions.length === 0) return null;

  if (layout === "chips") {
    return (
      /* Chips wrap rather than truncate: at 390px a one-line suggestion loses
         the half of the question that made it specific. */
      <div
        className={cn("flex flex-wrap gap-1.5", className)}
        {...(ariaLabel ? { role: "group", "aria-label": ariaLabel } : {})}
      >
        {suggestions.map((suggestion) => (
          <button
            key={suggestion.text}
            type="button"
            data-chat-suggestion={suggestion.source}
            onClick={() => onSelect(suggestion)}
            className="rounded-full border border-[var(--color-border-default)] px-2.5 py-1 text-left text-xs text-[var(--color-text-tertiary)] hover:border-[var(--color-border-hover)] hover:text-[var(--color-text-primary)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent-primary)]"
          >
            {suggestion.text}
          </button>
        ))}
      </div>
    );
  }

  return (
    <div
      className={className}
      {...(label || ariaLabel ? { role: "group", "aria-label": ariaLabel ?? label } : {})}
    >
      {label && (
        <p className="mb-1 font-mono text-[10px] uppercase tracking-[0.12em] text-[var(--color-text-tertiary)]">
          {label}
        </p>
      )}
      <ul className="border-t border-[var(--color-border-default)]">
        {suggestions.map((suggestion) => (
          <li key={suggestion.text}>
            <button
              type="button"
              data-chat-suggestion={suggestion.source}
              className="group flex w-full items-center gap-3 border-b border-[var(--color-border-default)] py-3 text-left text-[15px] text-[var(--color-text-secondary)] transition-colors hover:text-[var(--color-text-primary)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent-primary)]"
              onClick={() => onSelect(suggestion)}
            >
              <span className="min-w-0 flex-1">{suggestion.text}</span>
              <Send
                aria-hidden
                className="h-3.5 w-3.5 shrink-0 opacity-0 transition-opacity group-hover:opacity-100 group-focus-visible:opacity-100"
              />
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}
