"use client";

import { memo } from "react";
import { cn } from "../lib/cn";
import { BrandMark } from "../shared/brand-mark";
import { ToolCallGroup } from "./tool-call-group";
import { Markdown } from "../shared/markdown";
import { SourceCitations, type SourceReference } from "./source-citations";
import type { ChatUIMessage } from "@repowise-dev/types/chat";

interface ChatMessageProps {
  message: ChatUIMessage;
  repoId: string;
  onViewArtifact?: (artifact: { type: string; data: Record<string, unknown> }) => void;
  /** Optional avatar src for the assistant. Defaults to `/repowise-logo.png`. */
  assistantAvatarSrc?: string;
  /** Forwarded to `SourceCitations` so consumers can customise the link path. */
  buildCitationHref?: (source: SourceReference) => string;
  /** Forwarded to `SourceCitations` for route-agnostic link generation. */
  linkPrefix?: string;
}

/**
 * One turn of the transcript.
 *
 * The user's turn used to be a solid accent bubble with an accent avatar disc —
 * the highest-contrast object on the page, spent on the one element that is
 * purely a record of what you already typed. The accent belongs to things that
 * respond. A question now reads as the heading it functionally is, and the
 * answer below it gets the page.
 *
 * Memoised: without it every SSE token re-renders the whole list, which means
 * react-markdown re-parses every prior reply on every frame of a stream. The
 * cost is invisible in the JSX and scales with transcript length.
 */
function ChatMessageImpl({
  message,
  repoId,
  onViewArtifact,
  assistantAvatarSrc = "/repowise-logo.png",
  buildCitationHref,
  linkPrefix,
}: ChatMessageProps) {
  const isUser = message.role === "user";

  if (isUser) {
    return (
      <div className="flex gap-3">
        <span
          aria-hidden
          className="mt-[0.7rem] h-px w-6 shrink-0 bg-[var(--color-border-default)]"
        />
        <p className="flex-1 min-w-0 text-lg font-medium leading-snug text-[var(--color-text-primary)]">
          {message.text}
        </p>
      </div>
    );
  }

  return (
    <div className="flex gap-3.5">
      <div
        className={cn(
          "flex h-8 w-8 shrink-0 items-center justify-center rounded-full mt-0.5",
          "bg-[var(--color-bg-surface)] border border-[var(--color-border-default)]",
        )}
      >
        <BrandMark darkSrc={assistantAvatarSrc} size={22} />
      </div>

      <div className="flex-1 min-w-0">
        <div className="max-w-full space-y-3">
          {message.toolCalls.length > 0 && (
            <ToolCallGroup
              toolCalls={message.toolCalls}
              {...(onViewArtifact ? { onViewArtifact } : {})}
            />
          )}

          {message.text && <Markdown content={message.text} />}

          {!message.isStreaming && message.toolCalls.length > 0 && (
            <SourceCitations
              toolCalls={message.toolCalls}
              repoId={repoId}
              {...(linkPrefix ? { linkPrefix } : {})}
              {...(buildCitationHref ? { buildHref: buildCitationHref } : {})}
            />
          )}

          {message.isStreaming &&
            !message.text &&
            message.toolCalls.length === 0 && (
              <div className="flex items-center gap-1.5 py-2">
                <div className="h-1.5 w-1.5 rounded-full bg-[var(--color-text-tertiary)] animate-pulse" />
                <div
                  className="h-1.5 w-1.5 rounded-full bg-[var(--color-text-tertiary)] animate-pulse"
                  style={{ animationDelay: "0.15s" }}
                />
                <div
                  className="h-1.5 w-1.5 rounded-full bg-[var(--color-text-tertiary)] animate-pulse"
                  style={{ animationDelay: "0.3s" }}
                />
              </div>
            )}
        </div>
      </div>
    </div>
  );
}

export const ChatMessage = memo(ChatMessageImpl);
