"use client";

import { createContext, useContext, useMemo, type ReactNode } from "react";
import type { ChatHandoff, ChatSelection } from "@repowise-dev/types/chat";

export type { ChatHandoff, ChatSelection } from "@repowise-dev/types/chat";

/** Longest selection carried into the composer. Past this the quote stops
 *  being a question and starts being a paste of the file. */
const MAX_SELECTION_CHARS = 2000;

export interface ChatHandoffValue {
  /** Ask chat about one thing on the page. */
  request: (handoff: ChatHandoff) => void;
  /** Whether page-level "Ask about this" controls should render at all. */
  askEnabled: boolean;
  /** Whether the floating selection control should render at all. */
  selectionEnabled: boolean;
}

const DISABLED: ChatHandoffValue = {
  request: () => {},
  askEnabled: false,
  selectionEnabled: false,
};

/** Defaults to disabled so a surface rendered outside a chat host stays
 *  unchanged rather than growing a control that leads nowhere. */
const ChatHandoffContext = createContext<ChatHandoffValue>(DISABLED);

export function ChatHandoffProvider({
  onHandoff,
  askEnabled = true,
  selectionEnabled = true,
  children,
}: {
  onHandoff: (handoff: ChatHandoff) => void;
  askEnabled?: boolean;
  selectionEnabled?: boolean;
  children: ReactNode;
}) {
  const value = useMemo<ChatHandoffValue>(
    () => ({ request: onHandoff, askEnabled, selectionEnabled }),
    [onHandoff, askEnabled, selectionEnabled],
  );
  return (
    <ChatHandoffContext.Provider value={value}>
      {children}
    </ChatHandoffContext.Provider>
  );
}

export function useChatHandoff(): ChatHandoffValue {
  return useContext(ChatHandoffContext);
}

function formatSelectionRange(selection: ChatSelection): string {
  const { path, startLine, endLine } = selection;
  if (!path) return "";
  if (startLine === undefined) return path;
  if (endLine === undefined || endLine === startLine) return `${path}:${startLine}`;
  return `${path}:${startLine}-${endLine}`;
}

/**
 * The composer text one handoff seeds, shared so both hosts and every surface
 * quote a selection the same way.
 */
export function buildHandoffDraft(handoff: ChatHandoff): string {
  const question = handoff.question?.trim() ?? "";
  const selection = handoff.selection;
  const text = selection?.text.trim();
  if (!text) return question;

  const truncated = text.length > MAX_SELECTION_CHARS;
  const body = truncated ? `${text.slice(0, MAX_SELECTION_CHARS)}\n...` : text;
  // Longer than the longest run inside, so a selection that is itself fenced
  // (a quoted code block, a copied transcript) cannot close the quote early.
  const fence = "`".repeat(
    Math.max(3, ...[...body.matchAll(/`+/g)].map((run) => run[0].length + 1)),
  );
  const range = formatSelectionRange(selection!);
  const heading = [
    range ? `From ${range}` : "Selected text",
    truncated ? `(first ${MAX_SELECTION_CHARS} characters)` : "",
  ]
    .filter(Boolean)
    .join(" ");

  return [question, `${heading}:`, fence, body, fence].filter(Boolean).join("\n\n");
}
