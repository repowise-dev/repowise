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
 * messages, the empty-state suggestions, the input area, and slots.
 *
 * Chat is a reading surface: the transcript sits on `--color-bg-root` and the
 * one chrome row above it on `--color-bg-surface`. There is exactly one chrome
 * row — the page used to stack its own repo header on top of this one, under a
 * breadcrumb, so three hairlines ran before the first word of content.
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
import { BrandMark } from "../shared/brand-mark";
import { ChatMessage } from "./chat-message";
import { ArtifactPanel, type Artifact } from "./artifact-panel";
import type { ChatUIMessage } from "@repowise-dev/types/chat";
import type { SourceReference } from "./source-citations";

const DEFAULT_SUGGESTIONS = [
  "Give me an overview of this codebase",
  "What are the highest-risk files to modify?",
  "Score the change risk of HEAD",
  "What dead code can be safely removed?",
  "What architectural decisions have been made?",
  "Search for authentication-related code",
];

const MICRO_LABEL =
  "font-mono text-[10px] uppercase tracking-[0.12em] text-[var(--color-text-tertiary)]";

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
  /** Forwarded to `SourceCitations` for route-agnostic link generation. */
  linkPrefix?: string;
  /** Logo shown above the empty-state heading. */
  emptyStateLogoSrc?: string;
  /** Override default suggestion chips. */
  suggestions?: string[];
  /** Orientation line under the empty-state subtitle — index status, page
   *  counts, branch, last sync. Keeps the blank page honest about what's
   *  loaded, and is the only figure on it, so it is not buried. */
  statusSlot?: ReactNode;
  /** Disables the composer (e.g. no chat provider configured). */
  sendDisabled?: boolean;
  /** Banner shown above the composer when sending is disabled. */
  sendDisabledReason?: ReactNode;
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
  linkPrefix,
  emptyStateLogoSrc = "/repowise-logo.png",
  suggestions = DEFAULT_SUGGESTIONS,
  statusSlot,
  sendDisabled = false,
  sendDisabledReason,
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

  // Pulse the artifact-panel button when a new artifact lands while the
  // panel is closed, so streamed diagrams don't arrive silently.
  const prevArtifactCount = useRef(0);
  const [artifactPulse, setArtifactPulse] = useState(false);
  const totalArtifactCount = messages.reduce(
    (count, m) => count + m.toolCalls.filter((tc) => tc.artifact).length,
    0,
  );
  useEffect(() => {
    if (totalArtifactCount > prevArtifactCount.current && !artifactPanelOpen) {
      setArtifactPulse(true);
      const t = setTimeout(() => setArtifactPulse(false), 2500);
      prevArtifactCount.current = totalArtifactCount;
      return () => clearTimeout(t);
    }
    prevArtifactCount.current = totalArtifactCount;
    return undefined;
  }, [totalArtifactCount, artifactPanelOpen]);

  async function handleSubmit() {
    const text = input.trim();
    if (!text || isStreaming || sendDisabled) return;
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

  return (
    <div className="flex h-full flex-col min-h-0">
      {/* The one chrome row. */}
      {(historySlot || modelSelectorSlot) && (
        <div className="flex items-center justify-between gap-2 px-[var(--page-pad)] py-2.5 border-b border-[var(--color-border-default)] shrink-0 bg-[var(--color-bg-surface)]">
          <div className="flex min-w-0 items-center gap-2">{historySlot}</div>
          <div className="flex items-center gap-2 shrink-0">
            {totalArtifactCount > 0 && (
              <Button
                variant="ghost"
                size="sm"
                className={cn(
                  "h-8 text-xs gap-1.5 tabular-nums",
                  artifactPulse &&
                    "animate-pulse text-[var(--color-accent-primary)]",
                )}
                onClick={() => setArtifactPanelOpen(true)}
              >
                <PanelRight className="h-4 w-4" />
                <span className="sr-only sm:not-sr-only">Artifacts</span>
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
          // Scrolls: at 390x667 the mark, heading, status and six suggestions
          // exceed the viewport, and the old centred flex column clipped them.
          // Top-anchored rather than centred — vertical centring would need a
          // height Radix's `display:table` viewport wrapper does not pass down,
          // and the section style reads left-aligned everywhere else anyway.
          <ScrollArea className="h-full">
            <div className="mx-auto flex max-w-2xl flex-col gap-10 px-[var(--page-pad)] py-14">
              <div className="space-y-4">
                <BrandMark darkSrc={emptyStateLogoSrc} size={40} />
                <h2 className="text-[22px] font-semibold text-[var(--color-text-primary)]">
                  Ask anything about {repoName ?? "this codebase"}
                </h2>
                <p className="text-base text-[var(--color-text-secondary)] leading-relaxed">
                  Explore architecture, assess risk, search code, trace
                  dependencies, and understand decisions. Every answer cites the
                  pages it read.
                </p>
                {statusSlot && (
                  <p className="font-mono text-xs text-[var(--color-text-tertiary)] tabular-nums">
                    {statusSlot}
                  </p>
                )}
              </div>

              {/* Hairline rows, not a grid of bordered boxes. A suggestion is
                  not a discrete object you act on repeatedly; it is a list. */}
              <div>
                <p className={cn(MICRO_LABEL, "mb-1")}>Start with</p>
                <ul className="border-t border-[var(--color-border-default)]">
                  {suggestions.map((s) => (
                    <li key={s}>
                      <button
                        className="group flex w-full items-center gap-3 border-b border-[var(--color-border-default)] py-3 text-left text-[15px] text-[var(--color-text-secondary)] transition-colors hover:text-[var(--color-accent-primary)]"
                        onClick={() => handleSuggestion(s)}
                      >
                        <span className="flex-1 min-w-0">{s}</span>
                        <Send className="h-3.5 w-3.5 shrink-0 opacity-0 transition-opacity group-hover:opacity-100" />
                      </button>
                    </li>
                  ))}
                </ul>
              </div>
            </div>
          </ScrollArea>
        ) : (
          <ScrollArea className="h-full">
            <div className="px-[var(--page-pad)] py-10 space-y-10 max-w-3xl mx-auto">
              {messages.map((m) => (
                <ChatMessage
                  key={m.id}
                  message={m}
                  repoId={repoId}
                  onViewArtifact={handleViewArtifact}
                  {...(assistantAvatarSrc ? { assistantAvatarSrc } : {})}
                  {...(buildCitationHref ? { buildCitationHref } : {})}
                  {...(linkPrefix ? { linkPrefix } : {})}
                />
              ))}
              {error && (
                <div className="rounded-lg border border-[var(--color-error)]/30 bg-[var(--color-error)]/10 px-4 py-2.5 text-sm text-[var(--color-error)]">
                  {error}
                </div>
              )}
              <div ref={bottomRef} />
            </div>
          </ScrollArea>
        )}
      </div>

      {/* Input area. The composer keeps its elevation: it is a genuinely
          interactive surface, which is what rule 1 reserves elevation for. */}
      <div
        className={cn(
          "shrink-0 px-[var(--page-pad)] pb-5 pt-4",
          !isEmpty && "border-t border-[var(--color-border-default)]",
        )}
      >
        <div className="max-w-3xl mx-auto">
          {sendDisabled && sendDisabledReason && (
            <div className="mb-2 rounded-lg border border-[var(--color-warning)]/40 bg-[var(--color-warning)]/10 px-3 py-2 text-xs text-[var(--color-text-secondary)]">
              {sendDisabledReason}
            </div>
          )}
          <div
            className={cn(
              "flex items-end gap-2 rounded-2xl border border-[var(--color-border-default)] bg-[var(--color-bg-elevated)] px-4 py-3",
              sendDisabled && "opacity-60",
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
              placeholder="Ask a question, or paste a file path"
              aria-label="Chat message"
              disabled={sendDisabled}
              rows={1}
              className="flex-1 resize-none bg-transparent text-[15px] text-[var(--color-text-primary)] placeholder:text-[var(--color-text-tertiary)] outline-none leading-6 max-h-36 overflow-y-auto"
              style={{ scrollbarWidth: "none" }}
            />
            <Button
              size="icon"
              className="h-8 w-8 shrink-0 rounded-xl"
              onClick={isStreaming ? onCancel : () => void handleSubmit()}
              disabled={(!input.trim() && !isStreaming) || sendDisabled}
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
            <p className={cn(MICRO_LABEL, "mt-2.5 text-center")}>
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
