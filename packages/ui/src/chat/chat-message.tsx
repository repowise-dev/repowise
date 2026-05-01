"use client";

import { User } from "lucide-react";
import { cn } from "../lib/cn";
import { ToolCallBlock } from "./tool-call-block";
import { ChatMarkdown } from "./chat-markdown";
import { SourceCitations } from "./source-citations";
import type { ChatUIMessage } from "@repowise-dev/types/chat";

interface ChatMessageProps {
  message: ChatUIMessage;
  repoId: string;
  onViewArtifact?: (artifact: { type: string; data: Record<string, unknown> }) => void;
  /** Optional avatar src for the assistant. Defaults to `/repowise-logo.png`. */
  assistantAvatarSrc?: string;
}

export function ChatMessage({
  message,
  repoId,
  onViewArtifact,
  assistantAvatarSrc = "/repowise-logo.png",
}: ChatMessageProps) {
  const isUser = message.role === "user";

  return (
    <div className={cn("flex gap-3.5", isUser && "flex-row-reverse")}>
      <div
        className={cn(
          "flex shrink-0 items-center justify-center rounded-full mt-0.5",
          isUser
            ? "h-8 w-8 bg-[var(--color-accent-primary)]"
            : "h-8 w-8 bg-[var(--color-bg-elevated)] border border-[var(--color-border-default)]",
        )}
      >
        {isUser ? (
          <User className="h-4 w-4 text-white" />
        ) : (
          /* eslint-disable-next-line @next/next/no-img-element */
          <img
            src={assistantAvatarSrc}
            alt="repowise"
            width={22}
            height={22}
            className="drop-shadow-[0_0_4px_rgba(245,149,32,0.25)]"
          />
        )}
      </div>

      <div
        className={cn(
          "flex-1 min-w-0",
          isUser && "flex flex-col items-end",
        )}
      >
        {isUser && (
          <div className="rounded-2xl rounded-tr-sm bg-[var(--color-accent-primary)] px-4 py-2.5 text-sm text-white max-w-[85%]">
            {message.text}
          </div>
        )}

        {!isUser && (
          <div className="max-w-full space-y-1.5">
            {message.toolCalls.length > 0 && (
              <div className="space-y-1">
                {message.toolCalls.map((tc) => {
                  const artifact = tc.artifact;
                  const handler =
                    artifact && onViewArtifact
                      ? () => onViewArtifact(artifact)
                      : undefined;
                  return (
                    <ToolCallBlock
                      key={tc.id}
                      toolCall={tc}
                      {...(handler ? { onViewArtifact: handler } : {})}
                    />
                  );
                })}
              </div>
            )}

            {message.text && (
              <div className="prose-chat">
                <ChatMarkdown content={message.text} />
              </div>
            )}

            {!message.isStreaming && message.toolCalls.length > 0 && (
              <SourceCitations toolCalls={message.toolCalls} repoId={repoId} />
            )}

            {message.isStreaming &&
              !message.text &&
              message.toolCalls.length === 0 && (
                <div className="flex items-center gap-1.5 py-2">
                  <div className="h-1.5 w-1.5 rounded-full bg-[var(--color-accent-primary)] animate-pulse" />
                  <div
                    className="h-1.5 w-1.5 rounded-full bg-[var(--color-accent-primary)] animate-pulse"
                    style={{ animationDelay: "0.15s" }}
                  />
                  <div
                    className="h-1.5 w-1.5 rounded-full bg-[var(--color-accent-primary)] animate-pulse"
                    style={{ animationDelay: "0.3s" }}
                  />
                </div>
              )}
          </div>
        )}
      </div>
    </div>
  );
}
