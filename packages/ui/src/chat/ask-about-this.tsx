"use client";

import { MessageCircleQuestion } from "lucide-react";
import type { ChatContext, ChatSelection } from "@repowise-dev/types/chat";
import { cn } from "../lib/cn";
import { useChatHandoff } from "./chat-handoff";

export interface AskAboutThisProps {
  /** The thing this control asks about, not the route the reader is on. */
  context: ChatContext;
  /** Seeded first question. Specific to the surface, not a generic prompt. */
  question: string;
  /** Accessible name. Defaults to the one primary verb plus the subject. */
  label?: string;
  selection?: ChatSelection;
  /** Ask immediately instead of seeding the composer. */
  autoSend?: boolean;
  className?: string;
}

/**
 * The one page-level entry point into chat.
 *
 * Neutral at rest so it does not compete with the surface's own actions, and
 * absent entirely when the reader has switched these controls off.
 */
export function AskAboutThis({
  context,
  question,
  label,
  selection,
  autoSend,
  className,
}: AskAboutThisProps) {
  const { request, askEnabled } = useChatHandoff();
  if (!askEnabled) return null;

  const name = label ?? `Ask about ${context.label}`;
  return (
    <button
      type="button"
      title={name}
      aria-label={name}
      onClick={(event) => {
        event.stopPropagation();
        request({
          context,
          question,
          ...(selection ? { selection } : {}),
          ...(autoSend ? { autoSend } : {}),
        });
      }}
      className={cn(
        "inline-flex h-7 w-7 shrink-0 items-center justify-center rounded-md text-[var(--color-text-tertiary)] transition-colors hover:bg-[var(--color-bg-elevated)] hover:text-[var(--color-text-primary)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent-primary)] motion-reduce:transition-none",
        className,
      )}
    >
      <MessageCircleQuestion className="h-4 w-4" aria-hidden />
    </button>
  );
}
