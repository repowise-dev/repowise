"use client";

import { useRef, useEffect, useState, useCallback } from "react";
import Image from "next/image";
import { Send, StopCircle, PanelRight, MessageSquare } from "lucide-react";
import { Button } from "@/components/ui/button";
import { ScrollArea } from "@/components/ui/scroll-area";
import { useChat } from "@/lib/hooks/use-chat";
import { ChatMessage } from "./chat-message";
import { ModelSelector } from "./model-selector";
import { ConversationHistory } from "./conversation-history";
import { ArtifactPanel, type Artifact } from "./artifact-panel";
import { cn } from "@/lib/utils/cn";

interface ChatInterfaceProps {
  repoId: string;
  repoName?: string;
}

const DEFAULT_SUGGESTIONS = [
  "Give me an overview of this codebase",
  "What are the highest-risk files to modify?",
  "Show me the architecture diagram",
  "What dead code can be safely removed?",
  "What architectural decisions have been made?",
  "Search for authentication-related code",
];

export function ChatInterface({ repoId, repoName }: ChatInterfaceProps) {
  const {
    messages,
    conversationId,
    isStreaming,
    error,
    sendMessage,
    loadConversation,
    reset,
  } = useChat(repoId);

  const [input, setInput] = useState("");
  const [artifactPanelOpen, setArtifactPanelOpen] = useState(false);
  const [artifacts, setArtifacts] = useState<Artifact[]>([]);
  const bottomRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  const isEmpty = messages.length === 0;

  // Auto-scroll to bottom
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  // Auto-resize textarea
  useEffect(() => {
    const ta = textareaRef.current;
    if (ta) {
      ta.style.height = "auto";
      ta.style.height = `${Math.min(ta.scrollHeight, 144)}px`;
    }
  }, [input]);

  const handleViewArtifact = useCallback(
    (artifact: { type: string; data: Record<string, unknown> }) => {
      const title = (artifact.data.title as string) ?? artifact.type;
      const newArt: Artifact = { type: artifact.type, title, data: artifact.data };

      setArtifacts((prev) => {
        const existing = prev.findIndex(
          (a) => a.type === newArt.type && a.title === newArt.title,
        );
        if (existing >= 0) return prev;
        return [...prev, newArt];
      });
      setArtifactPanelOpen(true);
    },
    [],
  );

  async function handleSubmit() {
    const text = input.trim();
    if (!text || isStreaming) return;
    setInput("");
    if (textareaRef.current) {
      textareaRef.current.style.height = "auto";
    }
    await sendMessage(text);
  }

  function handleSuggestion(text: string) {
    setInput(text);
    textareaRef.current?.focus();
  }

  // Count artifacts from all messages
  const totalArtifactCount = messages.reduce(
    (count, m) =>
      count + m.toolCalls.filter((tc) => tc.artifact).length,
    0,
  );

  return (
    <div className="flex h-full flex-col min-h-0">
      {/* Header bar (when active conversation) */}
      {!isEmpty && (
        <div className="flex items-center justify-between px-4 py-2.5 border-b border-[var(--color-border-default)] shrink-0 bg-[var(--color-bg-surface)]/95 backdrop-blur-sm">
          <div className="flex items-center gap-2">
            <ConversationHistory
              repoId={repoId}
              activeConversationId={conversationId}
              onSelect={loadConversation}
              onNew={reset}
            />
          </div>
          <div className="flex items-center gap-2">
            {totalArtifactCount > 0 && (
              <Button
                variant="ghost"
                size="sm"
                className="h-8 text-xs gap-1.5"
                onClick={() => setArtifactPanelOpen(true)}
              >
                <PanelRight className="h-4 w-4" />
                {totalArtifactCount}
              </Button>
            )}
            <ModelSelector />
          </div>
        </div>
      )}

      {/* Message list or empty state */}
      <div className="flex-1 min-h-0 relative">
        {isEmpty ? (
          <div className="flex flex-col items-center justify-center h-full gap-10 px-4">
            <div className="text-center space-y-3">
              <div className="flex items-center justify-center mb-6">
                <Image
                  src="/repowise-logo.png"
                  alt="repowise"
                  width={48}
                  height={48}
                  className="drop-shadow-[0_0_12px_rgba(245,149,32,0.35)]"
                />
              </div>
              <h2 className="text-xl font-semibold text-[var(--color-text-primary)]">
                Ask anything about {repoName ?? "this codebase"}
              </h2>
              <p className="text-sm text-[var(--color-text-secondary)] max-w-md leading-relaxed">
                Explore architecture, assess risk, search code, trace
                dependencies, and understand decisions.
              </p>
            </div>

            {/* Suggestion chips */}
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-2.5 max-w-xl w-full">
              {DEFAULT_SUGGESTIONS.map((s) => (
                <button
                  key={s}
                  className="text-left text-sm text-[var(--color-text-secondary)] rounded-xl border border-[var(--color-border-default)] px-4 py-3 hover:bg-[var(--color-bg-elevated)] hover:text-[var(--color-text-primary)] hover:border-[var(--color-border-hover)] transition-colors"
                  onClick={() => handleSuggestion(s)}
                >
                  {s}
                </button>
              ))}
            </div>

            {/* Model selector for empty state */}
            <div className="flex items-center gap-3">
              <span className="text-xs text-[var(--color-text-tertiary)]">
                Using:
              </span>
              <ModelSelector />
              <ConversationHistory
                repoId={repoId}
                activeConversationId={null}
                onSelect={loadConversation}
                onNew={reset}
              />
            </div>
          </div>
        ) : (
          <ScrollArea className="h-full">
            <div className="px-4 py-6 space-y-5 max-w-3xl mx-auto">
              {messages.map((m) => (
                <ChatMessage
                  key={m.id}
                  message={m}
                  repoId={repoId}
                  onViewArtifact={handleViewArtifact}
                />
              ))}
              {error && (
                <div className="rounded-lg border border-red-500/30 bg-red-500/10 px-4 py-2.5 text-sm text-red-400">
                  {error}
                </div>
              )}
              <div ref={bottomRef} />
            </div>
          </ScrollArea>
        )}
      </div>

      {/* Input area */}
      <div
        className={cn(
          "shrink-0 px-4 py-4",
          !isEmpty && "border-t border-[var(--color-border-default)]",
        )}
      >
        <div className="max-w-3xl mx-auto">
          <div
            className={cn(
              "flex items-end gap-2 rounded-2xl border border-[var(--color-border-default)] bg-[var(--color-bg-elevated)] px-4 py-3",
              isEmpty && "shadow-lg shadow-black/20",
            )}
          >
            <textarea
              ref={textareaRef}
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  handleSubmit();
                }
              }}
              placeholder="Ask anything about this codebase..."
              aria-label="Chat message"
              rows={1}
              className="flex-1 resize-none bg-transparent text-sm text-[var(--color-text-primary)] placeholder:text-[var(--color-text-tertiary)] outline-none leading-6 max-h-36 overflow-y-auto"
              style={{ scrollbarWidth: "none" }}
            />
            <Button
              size="icon"
              className="h-8 w-8 shrink-0 rounded-xl"
              onClick={isStreaming ? reset : handleSubmit}
              disabled={!input.trim() && !isStreaming}
              aria-label={isStreaming ? "Stop generation" : "Send message"}
              title={isStreaming ? "Stop generation" : "Send message"}
            >
              {isStreaming ? (
                <StopCircle className="h-4 w-4" />
              ) : (
                <Send className="h-4 w-4" />
              )}
            </Button>
          </div>
          {isEmpty && (
            <p className="text-center text-[11px] text-[var(--color-text-tertiary)] mt-2.5">
              Shift+Enter for newline · Enter to send
            </p>
          )}
        </div>
      </div>

      {/* Artifact panel */}
      <ArtifactPanel
        artifacts={artifacts}
        open={artifactPanelOpen}
        onClose={() => setArtifactPanelOpen(false)}
      />
    </div>
  );
}
