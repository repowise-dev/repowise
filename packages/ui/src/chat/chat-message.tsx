"use client";

import { memo } from "react";
import { cn } from "../lib/cn";
import { ToolCallGroup } from "./tool-call-group";
import { WorkingOrb } from "./working-orb";
import { MessageActions } from "./message-actions";
import { Markdown } from "../shared/markdown";
import { SourceCitations, type SourceReference } from "./source-citations";
import type { ChatArtifact, ChatUIMessage } from "@repowise-dev/types/chat";

interface ChatMessageProps {
  message: ChatUIMessage;
  repoId: string;
  onViewArtifact?: (artifact: ChatArtifact) => void;
  /** Forwarded to `SourceCitations` so consumers can customise the link path. */
  buildCitationHref?: (source: SourceReference) => string;
  /** Forwarded to `SourceCitations` for route-agnostic link generation. */
  linkPrefix?: string;
  density?: "page" | "dock";
  /** True when this response uses a different model from the prior answer. */
  modelChanged?: boolean;
  /** The newest answer keeps its actions visible without hover. */
  isLatest?: boolean;
  /** Why this turn failed, shown inline with Retry. */
  error?: string | null;
  onRetry?: (message: ChatUIMessage) => void | Promise<void>;
  onEditAndResend?: (message: ChatUIMessage, text: string) => void | Promise<void>;
}

/**
 * One turn of the transcript, with no labels or avatars: the user's turn is a
 * quiet right-aligned bubble and the answer is plain prose on the page.
 *
 * Memoised: without it every SSE token re-renders the whole list, which means
 * react-markdown re-parses every prior reply on every frame of a stream.
 */
function ChatMessageImpl({
  message,
  repoId,
  onViewArtifact,
  buildCitationHref,
  linkPrefix,
  density = "page",
  modelChanged = false,
  isLatest = false,
  error,
  onRetry,
  onEditAndResend,
}: ChatMessageProps) {
  const isUser = message.role === "user";
  const dock = density === "dock";

  if (isUser) {
    return (
      <article
        aria-label="You"
        data-chat-message-id={message.id}
        data-chat-role="user"
        data-chat-density={density}
        className="group/turn flex min-w-0 flex-col items-end"
      >
        <p
          className={cn(
            "max-w-[85%] whitespace-pre-wrap rounded-2xl bg-[var(--color-bg-elevated)] px-4 py-2.5 leading-relaxed text-[var(--color-text-primary)] [overflow-wrap:anywhere]",
            dock ? "text-sm" : "text-base",
          )}
        >
          {message.text}
        </p>
        <MessageActions
          message={message}
          align="end"
          {...(onEditAndResend ? { onEditAndResend: (text) => onEditAndResend(message, text) } : {})}
        />
      </article>
    );
  }

  return (
    <article
      aria-label="Repowise"
      data-chat-message-id={message.id}
      data-chat-role="assistant"
      data-chat-density={density}
      className="group/turn min-w-0"
    >
      {modelChanged && (message.provider || message.model) && (
        <p className="mb-2 text-xs text-[var(--color-text-tertiary)]">
          Model changed to {[message.provider, message.model].filter(Boolean).join(" · ")}
        </p>
      )}
      <div className={cn("max-w-full", dock ? "space-y-2.5" : "space-y-3")}>
        {message.toolCalls.length > 0 && (
          <ToolCallGroup
            toolCalls={message.toolCalls}
            {...(onViewArtifact ? { onViewArtifact } : {})}
          />
        )}

        {message.text && (
          <Markdown content={message.text} density={dock ? "compact" : "reading"} streaming={message.isStreaming} />
        )}

        {!message.isStreaming && message.toolCalls.length > 0 && (
          <SourceCitations
            toolCalls={message.toolCalls}
            repoId={repoId}
            {...(linkPrefix ? { linkPrefix } : {})}
            {...(buildCitationHref ? { buildHref: buildCitationHref } : {})}
          />
        )}

        {message.truncated && !message.isStreaming && (
          <p
            data-chat-truncated="true"
            className="text-xs text-[var(--color-text-tertiary)]"
          >
            Stopped at the step limit before a final answer. Ask again to continue.
          </p>
        )}

        {message.isStreaming &&
          !message.text &&
          !message.toolCalls.some((tc) => tc.status === "running") && (
            <div className="flex items-center gap-2 py-2 text-xs text-[var(--color-text-tertiary)]">
              <WorkingOrb />
              <span>Reading context</span>
            </div>
          )}
        <MessageActions
          message={message}
          pinned={isLatest}
          {...(error ? { error } : {})}
          {...(onRetry ? { onRetry: () => onRetry(message) } : {})}
        />
      </div>
    </article>
  );
}

export const ChatMessage = memo(ChatMessageImpl);
