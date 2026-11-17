"use client";

import { useState } from "react";
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
        "inline-flex h-7 w-7 shrink-0 items-center justify-center rounded-md text-[var(--color-text-tertiary)] transition-colors hover:bg-[var(--color-bg-elevated)] hover:text-[var(--color-text-primary)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-focus-ring)] motion-reduce:transition-none",
        className,
      )}
    >
      <MessageCircleQuestion className="h-4 w-4" aria-hidden />
    </button>
  );
}

/**
 * The same entry point as a text field, for a reading surface with room for
 * one: the reader types their own question about the subject, or submits an
 * empty field to ask the seeded one. Either way the subject travels as the
 * chat's context, so the answer is about this page rather than the repo.
 */
export function AskAboutThisField({
  context,
  question,
  placeholder,
  className,
}: Pick<AskAboutThisProps, "context" | "question" | "className"> & {
  /** Visible hint and accessible name, e.g. "Ask about this page". */
  placeholder: string;
}) {
  const { request, askEnabled } = useChatHandoff();
  const [draft, setDraft] = useState("");
  if (!askEnabled) return null;

  return (
    <form
      role="search"
      onSubmit={(event) => {
        event.preventDefault();
        const typed = draft.trim();
        request({ context, question: typed || question, ...(typed ? { autoSend: true } : {}) });
        setDraft("");
      }}
      className={cn(
        "flex h-7 min-w-0 items-center gap-1.5 rounded-md border border-[var(--color-border-default)] bg-[var(--color-bg-elevated)] px-2 transition-colors focus-within:border-[var(--color-border-hover)]",
        className,
      )}
    >
      <MessageCircleQuestion
        aria-hidden
        className="h-3.5 w-3.5 shrink-0 text-[var(--color-text-tertiary)]"
      />
      <input
        value={draft}
        onChange={(event) => setDraft(event.target.value)}
        placeholder={placeholder}
        aria-label={placeholder}
        className="min-w-0 flex-1 bg-transparent text-xs text-[var(--color-text-primary)] placeholder:text-[var(--color-text-tertiary)] focus:outline-none"
      />
    </form>
  );
}
