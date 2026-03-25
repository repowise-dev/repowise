"use client";

import { Bot, User } from "lucide-react";
import { cn } from "@/lib/utils/cn";
import { ToolCallBlock } from "./tool-call-block";
import { ChatMarkdown } from "./chat-markdown";
import type { ChatMessage as ChatMessageType } from "@/lib/hooks/use-chat";

interface ChatMessageProps {
  message: ChatMessageType;
  onViewArtifact?: (artifact: { type: string; data: Record<string, unknown> }) => void;
}

export function ChatMessage({ message, onViewArtifact }: ChatMessageProps) {
  const isUser = message.role === "user";

  return (
    <div className={cn("flex gap-3", isUser && "flex-row-reverse")}>
      {/* Avatar */}
      <div
        className={cn(
          "flex h-7 w-7 shrink-0 items-center justify-center rounded-full mt-0.5",
          isUser
            ? "bg-[var(--color-accent-primary)]"
            : "bg-[var(--color-bg-elevated)] border border-[var(--color-border-default)]",
        )}
      >
        {isUser ? (
          <User className="h-3.5 w-3.5 text-white" />
        ) : (
          <Bot className="h-3.5 w-3.5 text-[var(--color-text-tertiary)]" />
        )}
      </div>

      {/* Content */}
      <div
        className={cn(
          "flex-1 min-w-0",
          isUser && "flex flex-col items-end",
        )}
      >
        {/* User message */}
        {isUser && (
          <div className="rounded-2xl rounded-tr-sm bg-[var(--color-accent-primary)] px-3.5 py-2 text-sm text-white max-w-[85%]">
            {message.text}
          </div>
        )}

        {/* Assistant message */}
        {!isUser && (
          <div className="max-w-full space-y-1">
            {/* Tool calls */}
            {message.toolCalls.length > 0 && (
              <div className="space-y-1">
                {message.toolCalls.map((tc) => (
                  <ToolCallBlock
                    key={tc.id}
                    toolCall={tc}
                    onViewArtifact={
                      tc.artifact && onViewArtifact
                        ? () => onViewArtifact(tc.artifact!)
                        : undefined
                    }
                  />
                ))}
              </div>
            )}

            {/* Text content */}
            {message.text && (
              <div className="prose-chat">
                <ChatMarkdown content={message.text} />
              </div>
            )}

            {/* Streaming cursor */}
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
