"use client";

/**
 * Presentational shell for the main chat interface. The wrapper owns:
 *
 *   - the SSE transport (`useChat` in hosted-web, the federated transport in
 *     the hosted-frontend example app),
 *   - the model + conversation-history dropdowns (passed in as opaque slot
 *     `ReactNode`s so each consumer can wire its own data hooks),
 *   - artifact panel state (artifacts list + open boolean).
 *
 * The shell is stateless apart from the textarea input value and renders
 * messages, the empty-state suggestion chips, the input area, and slots.
 */

import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { Send, StopCircle, PanelRight } from "lucide-react";
import { Button } from "../ui/button";
import { ScrollArea } from "../ui/scroll-area";
import { cn } from "../lib/cn";
import { ChatMessage } from "./chat-message";
import { ArtifactPanel, type Artifact } from "./artifact-panel";
import type { ChatUIMessage } from "@repowise-dev/types/chat";
import type { SourceReference } from "./source-citations";

const DEFAULT_SUGGESTIONS = [
  "Give me an overview of this codebase",
  "What are the highest-risk files to modify?",
  "Show me the architecture diagram",
  "What dead code can be safely removed?",
  "What architectural decisions have been made?",
  "Search for authentication-related code",
];

export interface ChatInterfaceProps {
  /** Identifier forwarded to `ChatMessage` for source-citation hrefs. */
  repoId: string;
  /** Optional repo display name shown in the empty state heading. */
  repoName?: string;

  /** Conversation transcript (UI-flattened). */
  messages: ChatUIMessage[];
  /** True while a response is streaming; flips Send → Stop. */
  isStreaming: boolean;
  /** Optional inline error banner (cleared by the wrapper when appropriate). */
  error?: string | null;

  /** Submit a new user message. */
  onSend: (text: string) => void | Promise<void>;
  /** Cancel the in-flight stream. Also used as "reset" by callers. */
  onCancel: () => void;

  /**
   * Slot rendered in the right side of the active-conversation header bar AND
   * in the empty-state composer footer. Typically a `<ModelSelector />`
   * wrapper that owns its providers SWR.
   */
  modelSelectorSlot?: ReactNode;
  /**
   * Slot rendered in the left side of the active-conversation header bar AND
   * in the empty-state composer footer. Typically a `<ConversationHistory />`
   * wrapper that owns its SWR + delete mutation.
   */
  historySlot?: ReactNode;

  /** Avatar src forwarded to `ChatMessage`. */
  assistantAvatarSrc?: string;
  /** Forwarded to `SourceCitations` for href customisation. */
  buildCitationHref?: (source: SourceReference) => string;
  /** Logo shown above the empty-state heading. */
  emptyStateLogoSrc?: string;
  /** Override default suggestion chips. */
  suggestions?: string[];
}

export function ChatInterface({
  repoId,
  repoName,
  messages,
  isStreaming,
  error,
  onSend,
  onCancel,
  modelSelectorSlot,
  historySlot,
  assistantAvatarSrc,
  buildCitationHref,
  emptyStateLogoSrc = "/repowise-logo.png",
  suggestions = DEFAULT_SUGGESTIONS,
}: ChatInterfaceProps) {
  const [input, setInput] = useState("");
  const [artifactPanelOpen, setArtifactPanelOpen] = useState(false);
  const [artifacts, setArtifacts] = useState<Artifact[]>([]);
  const bottomRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  const isEmpty = messages.length === 0;

  useEffect(() => {
    bottomRef.current?.scrollIntoView?.({ behavior: "smooth" });
  }, [messages]);

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
    await onSend(text);
  }

  function handleSuggestion(text: string) {
    setInput(text);
    textareaRef.current?.focus();
  }

  const totalArtifactCount = messages.reduce(
    (count, m) => count + m.toolCalls.filter((tc) => tc.artifact).length,
    0,
  );

  return (
    <div className="flex h-full flex-col min-h-0">
      {/* Header bar (active conversation) */}
      {!isEmpty && (
        <div className="flex items-center justify-between px-4 py-2.5 border-b border-[var(--color-border-default)] shrink-0 bg-[var(--color-bg-surface)]/95 backdrop-blur-sm">
          <div className="flex items-center gap-2">{historySlot}</div>
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
            {modelSelectorSlot}
          </div>
        </div>
      )}

      {/* Message list or empty state */}
      <div className="flex-1 min-h-0 relative">
        {isEmpty ? (
          <div className="flex flex-col items-center justify-center h-full gap-10 px-4">
            <div className="text-center space-y-3">
              <div className="flex items-center justify-center mb-6">
                {/* eslint-disable-next-line @next/next/no-img-element */}
                <img
                  src={emptyStateLogoSrc}
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

            <div className="grid grid-cols-1 sm:grid-cols-2 gap-2.5 max-w-xl w-full">
              {suggestions.map((s) => (
                <button
                  key={s}
                  className="text-left text-sm text-[var(--color-text-secondary)] rounded-xl border border-[var(--color-border-default)] px-4 py-3 hover:bg-[var(--color-bg-elevated)] hover:text-[var(--color-text-primary)] hover:border-[var(--color-border-hover)] transition-colors"
                  onClick={() => handleSuggestion(s)}
                >
                  {s}
                </button>
              ))}
            </div>

            <div className="flex items-center gap-3">
              {modelSelectorSlot && (
                <span className="text-xs text-[var(--color-text-tertiary)]">
                  Using:
                </span>
              )}
              {modelSelectorSlot}
              {historySlot}
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
                  {...(assistantAvatarSrc ? { assistantAvatarSrc } : {})}
                  {...(buildCitationHref ? { buildCitationHref } : {})}
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
                  void handleSubmit();
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
              onClick={isStreaming ? onCancel : () => void handleSubmit()}
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

      <ArtifactPanel
        artifacts={artifacts}
        open={artifactPanelOpen}
        onClose={() => setArtifactPanelOpen(false)}
      />
    </div>
  );
}
