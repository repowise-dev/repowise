"use client";

import { memo } from "react";
import { UserRound } from "lucide-react";
import { cn } from "../lib/cn";
import { BrandMark } from "../shared/brand-mark";
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
  /** Optional avatar src for the assistant. Defaults to `/repowise-logo.png`. */
  assistantAvatarSrc?: string;
  /** Forwarded to `SourceCitations` so consumers can customise the link path. */
  buildCitationHref?: (source: SourceReference) => string;
  /** Forwarded to `SourceCitations` for route-agnostic link generation. */
  linkPrefix?: string;
  density?: "page" | "dock";
  /** Optional host identity image for user turns. */
  userAvatarSrc?: string;
  /** True when this response uses a different model from the prior answer. */
  modelChanged?: boolean;
  onRetry?: (message: ChatUIMessage) => void | Promise<void>;
  onEditAndResend?: (message: ChatUIMessage, text: string) => void | Promise<void>;
  onFollowUp?: (text: string) => void;
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
  density = "page",
  userAvatarSrc,
  modelChanged = false,
  onRetry,
  onEditAndResend,
  onFollowUp,
}: ChatMessageProps) {
  const isUser = message.role === "user";

  if (isUser) {
    return (
      <article
        aria-label="You"
        data-chat-message-id={message.id}
        data-chat-role="user"
        data-chat-density={density}
        className="flex min-w-0 justify-end gap-2.5"
      >
        <div className={cn("min-w-0", density === "dock" ? "max-w-[88%]" : "max-w-[78%]") }>
          <p className="mb-1 text-right font-mono text-[10px] uppercase tracking-[0.12em] text-[var(--color-text-tertiary)]">
            You
          </p>
          <p
            className={cn(
              "[overflow-wrap:anywhere] rounded-xl border border-[var(--color-border-default)] bg-[var(--color-bg-surface)] text-[var(--color-text-primary)]",
              density === "dock"
                ? "px-3 py-2 text-[15px] leading-relaxed"
                : "px-3.5 py-2.5 text-base leading-relaxed",
            )}
          >
            {message.text}
          </p>
          <MessageActions message={message} {...(onEditAndResend ? { onEditAndResend: (text) => onEditAndResend(message, text) } : {})} />
        </div>
        <div className="mt-5 flex h-7 w-7 shrink-0 items-center justify-center overflow-hidden rounded-full border border-[var(--color-border-default)] bg-[var(--color-bg-surface)] text-[var(--color-text-secondary)]">
          {userAvatarSrc ? (
            <img src={userAvatarSrc} alt="" className="h-full w-full object-cover" />
          ) : (
            <UserRound aria-hidden className="h-3.5 w-3.5" />
          )}
        </div>
      </article>
    );
  }

  return (
    <article
      aria-label="Repowise"
      data-chat-message-id={message.id}
      data-chat-role="assistant"
      data-chat-density={density}
      className={cn("flex min-w-0", density === "dock" ? "gap-2.5" : "gap-3.5")}
    >
      <div className="mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center">
        <BrandMark darkSrc={assistantAvatarSrc} size={22} />
      </div>

      <div className="flex-1 min-w-0">
        <p className="mb-2 font-mono text-[10px] uppercase tracking-[0.12em] text-[var(--color-text-tertiary)]">
          Repowise
        </p>
        {(message.provider || message.model) && (
          <p className="mb-2 text-[11px] text-[var(--color-text-tertiary)]">
            {modelChanged ? "Model changed to " : ""}
            {[message.provider, message.model].filter(Boolean).join(" · ")}
          </p>
        )}
        <div className={cn("max-w-full", density === "dock" ? "space-y-2.5" : "space-y-3")}>
          {message.toolCalls.length > 0 && (
            <ToolCallGroup
              toolCalls={message.toolCalls}
              {...(onViewArtifact ? { onViewArtifact } : {})}
            />
          )}

          {message.text && (
            <Markdown content={message.text} density={density === "dock" ? "compact" : "reading"} streaming={message.isStreaming} />
          )}

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
              <div className="flex items-center gap-2 py-2 text-xs text-[var(--color-text-tertiary)]">
                <WorkingOrb />
                <span>Reading context</span>
              </div>
            )}
          <MessageActions
            message={message}
            {...(onRetry ? { onRetry: () => onRetry(message) } : {})}
            {...(onFollowUp ? { onFollowUp } : {})}
          />
        </div>
      </div>
    </article>
  );
}

export const ChatMessage = memo(ChatMessageImpl);
