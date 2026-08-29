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
  useMemo,
  useRef,
  useState,
  type RefObject,
  type ReactNode,
} from "react";
import { ArrowDown, Send, PanelRight } from "lucide-react";
import { Button } from "../ui/button";
import { ActivityDot } from "../ui/activity-dot";
import { ScrollArea } from "../ui/scroll-area";
import { cn } from "../lib/cn";
import { BrandMark } from "../shared/brand-mark";
import { ChatMessage } from "./chat-message";
import { ArtifactPanel } from "./artifact-panel";
import { ChatContextIndicator } from "./chat-context-indicator";
import {
  getChatContextPresentation,
  type ChatContext,
} from "./chat-context";
import type { ChatArtifact, ChatUIMessage } from "@repowise-dev/types/chat";
import type { SourceReference } from "./source-citations";
import { ChatComposer } from "./chat-composer";
import { useChatScroll } from "./use-chat-scroll";

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
  /** Product surface surrounding this composer. */
  context?: ChatContext;

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
   * Slot rendered at the left side of the composer footer. Typically a `<ModelSelector />`
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
  /** Optional host-supplied avatar for user turns. */
  userAvatarSrc?: string;
  /** Forwarded to `SourceCitations` for href customisation. */
  buildCitationHref?: (source: SourceReference) => string;
  /** Forwarded to `SourceCitations` for route-agnostic link generation. */
  linkPrefix?: string;
  /** Logo shown above the empty-state heading. */
  emptyStateLogoSrc?: string;
  /** Override default suggestion chips. */
  suggestions?: readonly string[];
  /** Override the context-derived composer placeholder. */
  placeholder?: string;
  /** Orientation line under the empty-state subtitle — index status, page
   *  counts, branch, last sync. Keeps the blank page honest about what's
   *  loaded, and is the only figure on it, so it is not buried. */
  statusSlot?: ReactNode;
  /** Disables the composer (e.g. no chat provider configured). */
  sendDisabled?: boolean;
  /** Banner shown above the composer when sending is disabled. */
  sendDisabledReason?: ReactNode;
  /** Compact transcript treatment for a floating dock. */
  variant?: "page" | "dock";
  /** Optional controlled draft shared with another chat surface. */
  draft?: string;
  onDraftChange?: (draft: string) => void;
  onContextRemove?: () => void;
  composerRef?: RefObject<HTMLTextAreaElement | null>;
  onRetry?: (message: ChatUIMessage) => void | Promise<void>;
  onEditAndResend?: (message: ChatUIMessage, text: string) => void | Promise<void>;
  /** Controlled artifact deep links. Hosts own URL and persistence concerns. */
  activeArtifactId?: string | null;
  compareArtifactId?: string | null;
  onArtifactNavigate?: (artifactId: string | null) => void;
  onArtifactCompare?: (artifactId: string | null) => void;
  onArtifactPin?: (artifact: ChatArtifact, pinned: boolean) => void | Promise<void>;
  onOpenArtifactSource?: (artifact: ChatArtifact) => void;
  /** Host-side artifact overlays (for example a persisted pin response). */
  artifactOverrides?: Readonly<Record<string, ChatArtifact>>;
}

export function ChatInterface({
  repoId,
  repoName,
  context,
  messages,
  isStreaming,
  error,
  onSend,
  onCancel,
  modelSelectorSlot,
  historySlot,
  assistantAvatarSrc,
  userAvatarSrc,
  buildCitationHref,
  linkPrefix,
  emptyStateLogoSrc = "/repowise-logo.png",
  suggestions,
  placeholder,
  statusSlot,
  sendDisabled = false,
  sendDisabledReason,
  variant = "page",
  draft,
  onDraftChange,
  onContextRemove,
  composerRef,
  onRetry,
  onEditAndResend,
  activeArtifactId,
  compareArtifactId,
  onArtifactNavigate,
  onArtifactCompare,
  onArtifactPin,
  onOpenArtifactSource,
  artifactOverrides,
}: ChatInterfaceProps) {
  const [internalInput, setInternalInput] = useState("");
  const input = draft ?? internalInput;
  const setInput = onDraftChange ?? setInternalInput;
  const [artifactPanelOpen, setArtifactPanelOpen] = useState(false);
  const [internalArtifactId, setInternalArtifactId] = useState<string | null>(null);
  const [internalCompareId, setInternalCompareId] = useState<string | null>(null);
  const selectedArtifactId = activeArtifactId ?? internalArtifactId;
  const selectedCompareId = compareArtifactId ?? internalCompareId;
  const artifacts = useMemo(() => {
    const seen = new Set<string>();
    const restored: ChatArtifact[] = [];
    for (const message of messages) {
      for (const toolCall of message.toolCalls) {
        if (toolCall.artifact && !seen.has(toolCall.artifact.id)) {
          seen.add(toolCall.artifact.id);
          restored.push(artifactOverrides?.[toolCall.artifact.id] ?? toolCall.artifact);
        }
      }
    }
    return restored;
  }, [artifactOverrides, messages]);
  const internalTextareaRef = useRef<HTMLTextAreaElement>(null);
  const textareaRef = composerRef ?? internalTextareaRef;

  const isEmpty = messages.length === 0;
  const contextPresentation = getChatContextPresentation(context);
  const visibleSuggestions =
    suggestions ?? (context ? contextPresentation.suggestions : DEFAULT_SUGGESTIONS);
  const composerPlaceholder = placeholder ?? contextPresentation.placeholder;
  const showContext =
    context !== undefined &&
    context.kind !== "repository" &&
    context.kind !== "chat";

  const {
    viewportRef,
    contentRef,
    hasContentBelow,
    isFollowingLive,
    revealNewTurn,
    jumpToLatest,
  } = useChatScroll();
  const [announcement, setAnnouncement] = useState("");
  const previousStreaming = useRef(isStreaming);
  const cancelledRef = useRef(false);

  useEffect(() => {
    if (error) setAnnouncement(`Answer failed. ${error}`);
    else if (!previousStreaming.current && isStreaming) {
      cancelledRef.current = false;
      setAnnouncement("Working on your answer.");
    } else if (previousStreaming.current && !isStreaming) {
      if (!cancelledRef.current) setAnnouncement("Answer complete.");
    }
    previousStreaming.current = isStreaming;
  }, [error, isStreaming]);

  const handleSend = useCallback(
    (text: string) => {
      revealNewTurn();
      return onSend(text);
    },
    [onSend, revealNewTurn],
  );

  const handleCancel = useCallback(() => {
    cancelledRef.current = true;
    setAnnouncement("Answer stopped.");
    onCancel();
  }, [onCancel]);

  const handleFollowUp = useCallback(
    (text: string) => {
      setInput(text);
      textareaRef.current?.focus();
    },
    [setInput, textareaRef],
  );

  const handleViewArtifact = useCallback(
    (artifact: ChatArtifact) => {
      setInternalArtifactId(artifact.id);
      onArtifactNavigate?.(artifact.id);
      setArtifactPanelOpen(true);
    },
    [onArtifactNavigate],
  );

  const selectArtifact = useCallback((artifactId: string) => {
    setInternalArtifactId(artifactId);
    onArtifactNavigate?.(artifactId);
    if (artifactId === selectedCompareId) {
      setInternalCompareId(null);
      onArtifactCompare?.(null);
    }
  }, [onArtifactCompare, onArtifactNavigate, selectedCompareId]);
  const compareArtifact = useCallback((artifactId: string | null) => {
    const next = artifactId ? artifacts.find((item) => item.id !== artifactId)?.id ?? null : null;
    setInternalCompareId(next);
    onArtifactCompare?.(next);
  }, [artifacts, onArtifactCompare]);
  const closeArtifacts = useCallback(() => {
    setArtifactPanelOpen(false);
    onArtifactNavigate?.(null);
  }, [onArtifactNavigate]);

  useEffect(() => {
    if (activeArtifactId && artifacts.some((artifact) => artifact.id === activeArtifactId)) setArtifactPanelOpen(true);
  }, [activeArtifactId, artifacts]);

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

  function handleSuggestion(text: string) {
    setInput(text);
    textareaRef.current?.focus();
  }

  return (
    <div className="flex h-full min-h-0 flex-col" data-chat-variant={variant}>
      {/* The one chrome row. */}
      {(historySlot || totalArtifactCount > 0) && (
        <div className="flex items-center justify-between gap-2 px-[var(--page-pad)] py-2.5 border-b border-[var(--color-border-default)] shrink-0 bg-[var(--color-bg-surface)]">
          <div className="flex min-w-0 items-center gap-2">{historySlot}</div>
          <div className="flex items-center gap-2 shrink-0">
            {totalArtifactCount > 0 && (
              <Button
                variant="ghost"
                size="sm"
                className={cn(
                  "h-8 text-xs gap-1.5 tabular-nums",
                  artifactPulse && "text-[var(--color-accent-secondary)]",
                )}
                onClick={() => setArtifactPanelOpen(true)}
              >
                {artifactPulse ? (
                  <ActivityDot className="h-1.5 w-1.5 bg-[var(--color-accent-secondary)]" />
                ) : null}
                <PanelRight className="h-4 w-4" />
                <span className="sr-only sm:not-sr-only">Artifacts</span>
                {totalArtifactCount}
              </Button>
            )}
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
          <ScrollArea className="h-full" viewportRef={viewportRef}>
            <div ref={contentRef} className={cn("mx-auto flex max-w-2xl flex-col", variant === "dock" ? "gap-5 px-4 py-7" : "gap-10 px-[var(--page-pad)] py-14")}>
              <div className="space-y-4">
                {variant !== "dock" && <BrandMark darkSrc={emptyStateLogoSrc} size={40} />}
                <h2 className={cn("font-semibold text-[var(--color-text-primary)]", variant === "dock" ? "text-lg" : "text-[22px]")}>
                  {variant === "dock" ? "Ask from this view" : `Ask anything about ${repoName ?? "this codebase"}`}
                </h2>
                <p className={cn("text-[var(--color-text-secondary)] leading-relaxed", variant === "dock" ? "text-sm" : "text-base")}>
                  {variant === "dock"
                    ? "Keep investigating without leaving the page. Answers use the active repository context."
                    : "Explore architecture, assess risk, search code, trace dependencies, and understand decisions. Every answer cites the pages it read."}
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
                  {visibleSuggestions.slice(0, variant === "dock" ? 3 : undefined).map((s) => (
                    <li key={s}>
                      <button
                        className="group flex w-full items-center gap-3 border-b border-[var(--color-border-default)] py-3 text-left text-[15px] text-[var(--color-text-secondary)] transition-colors hover:text-[var(--color-text-primary)]"
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
          <ScrollArea
            className="h-full"
            viewportRef={viewportRef}
            viewportClassName="[overflow-anchor:auto]"
          >
            <div
              ref={contentRef}
              className={cn(
                "mx-auto w-full",
                variant === "dock"
                  ? "max-w-full space-y-6 px-4 py-5"
                  : "max-w-[960px] space-y-10 px-[var(--page-pad)] py-10",
              )}
            >
              {messages.map((m, index) => {
                const previousAssistant = messages
                  .slice(0, index)
                  .reverse()
                  .find((candidate) => candidate.role === "assistant");
                const modelChanged = Boolean(
                  m.role === "assistant" &&
                  previousAssistant &&
                  `${previousAssistant.provider ?? ""}:${previousAssistant.model ?? ""}` !==
                    `${m.provider ?? ""}:${m.model ?? ""}`,
                );
                return (
                <ChatMessage
                  key={m.id}
                  message={m}
                  repoId={repoId}
                  onViewArtifact={handleViewArtifact}
                  {...(assistantAvatarSrc ? { assistantAvatarSrc } : {})}
                  {...(userAvatarSrc ? { userAvatarSrc } : {})}
                  density={variant}
                  modelChanged={modelChanged}
                  {...(onRetry ? { onRetry } : {})}
                  {...(onEditAndResend ? { onEditAndResend } : {})}
                  onFollowUp={handleFollowUp}
                  {...(buildCitationHref ? { buildCitationHref } : {})}
                  {...(linkPrefix ? { linkPrefix } : {})}
                />
                );
              })}
              {error && (
                <div className="rounded-lg border border-[var(--color-error)]/30 bg-[var(--color-error)]/10 px-4 py-2.5 text-sm text-[var(--color-error)]">
                  {error}
                </div>
              )}
            </div>
          </ScrollArea>
        )}
        {!isEmpty && hasContentBelow && (
          <button
            type="button"
            onClick={jumpToLatest}
            aria-pressed={isFollowingLive}
            className="absolute bottom-3 left-1/2 z-10 inline-flex min-h-9 -translate-x-1/2 items-center gap-1.5 rounded-full border border-[var(--color-border-default)] bg-[var(--color-bg-overlay)] px-3 text-xs font-medium text-[var(--color-text-secondary)] shadow-[var(--shadow-sm)] hover:text-[var(--color-text-primary)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent-primary)]"
          >
            <ArrowDown className="h-3.5 w-3.5" />
            Jump to latest
          </button>
        )}
      </div>

      <span className="sr-only" role="status" aria-live="polite" aria-atomic="true">
        {announcement}
      </span>

      {/* Input area. The composer keeps its elevation: it is a genuinely
          interactive surface, which is what rule 1 reserves elevation for. */}
      <div
        className={cn(
          "shrink-0",
          variant === "dock" ? "px-3 pb-3 pt-3" : "px-[var(--page-pad)] pb-5 pt-4",
          !isEmpty && "border-t border-[var(--color-border-default)]",
        )}
      >
        <div className={cn("mx-auto w-full", variant === "dock" ? "max-w-full" : "max-w-[960px]")}>
          {sendDisabled && sendDisabledReason && (
            <div className="mb-2 rounded-lg border border-[var(--color-warning)]/40 bg-[var(--color-warning)]/10 px-3 py-2 text-xs text-[var(--color-text-secondary)]">
              {sendDisabledReason}
            </div>
          )}
          {showContext && context && (
            <ChatContextIndicator
              context={context}
              {...(onContextRemove ? { onRemove: onContextRemove } : {})}
            />
          )}
          <ChatComposer
            value={input}
            onValueChange={setInput}
            onSend={handleSend}
            onCancel={handleCancel}
            isStreaming={isStreaming}
            placeholder={composerPlaceholder}
            disabled={sendDisabled}
            compact={variant === "dock"}
            textareaRef={textareaRef}
            {...(modelSelectorSlot ? { footer: modelSelectorSlot } : {})}
          />
          {isEmpty && !modelSelectorSlot && (
            <p className={cn(MICRO_LABEL, "mt-2.5 text-center")}>
              Shift+Enter for newline · Enter to send
            </p>
          )}
        </div>
      </div>

      <ArtifactPanel
        artifacts={artifacts}
        activeArtifactId={selectedArtifactId}
        compareArtifactId={selectedCompareId}
        open={artifactPanelOpen}
        onClose={closeArtifacts}
        onSelect={selectArtifact}
        onCompare={compareArtifact}
        onFollowUp={handleFollowUp}
        {...(onArtifactPin ? { onPin: onArtifactPin } : {})}
        {...(onOpenArtifactSource ? { onOpenSource: onOpenArtifactSource } : {})}
      />
    </div>
  );
}
