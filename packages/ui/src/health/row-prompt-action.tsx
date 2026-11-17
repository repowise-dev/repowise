"use client";

import { cn } from "../lib/cn";
import { AiPromptButton } from "./ai-prompt-button";
import { AGENT_PROMPT_LABEL, bulkAgentPromptLabel } from "./ai-prompt-action";

/**
 * The per-row "hand this to your agent" action. Ghost (icon + label, no fill)
 * so a column of them does not shout, or icon-only for a dense table, and
 * revealed on row hover, keyboard focus within the row, and the selected row.
 * It stays in the tab order and shows on focus, and it is always visible
 * where there is no hover (touch).
 *
 * The row must carry `group/row` (and `data-selected` when selected), as
 * `ResponsiveTable` rows do. The click usually calls `openPrompt` from
 * {@link useAiPromptAction}, the one modal host.
 */
export function RowPromptAction({
  onClick,
  label = AGENT_PROMPT_LABEL,
  subject,
  variant = "ghost",
  className,
}: {
  onClick: () => void;
  label?: string;
  /** `icon` for a dense table row; the label is then the accessible name. */
  variant?: "ghost" | "icon";
  /** What the row is about, e.g. its path; names the action for screen readers. */
  subject?: string;
  className?: string;
}) {
  return (
    <AiPromptButton
      variant={variant}
      label={label}
      {...(subject ? { ariaLabel: `${label}: ${subject}` } : {})}
      onClick={onClick}
      className={cn(
        "whitespace-nowrap opacity-0 focus-visible:opacity-100 group-hover/row:opacity-100 group-focus-within/row:opacity-100 group-data-[selected=true]/row:opacity-100 [@media(hover:none)]:opacity-100",
        className,
      )}
    />
  );
}

/**
 * The header action: one prompt over every row the current filters
 * show. Renders nothing for an empty list, since a prompt about nothing is
 * not an action.
 */
export function PromptForRowsButton({
  count,
  onClick,
  label = bulkAgentPromptLabel,
  className,
}: {
  count: number;
  onClick: () => void;
  label?: (count: number) => string;
  className?: string;
}) {
  if (count === 0) return null;
  return (
    <AiPromptButton
      label={label(count)}
      onClick={onClick}
      className={cn("whitespace-nowrap", className)}
    />
  );
}
