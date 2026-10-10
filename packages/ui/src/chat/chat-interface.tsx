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
 * Chat is a reading surface: one 720px column on `--color-bg-root` holds the
 * transcript, the empty state and the composer. History and artifacts sit in
 * a borderless header row that is always mounted, so nothing shifts when the
 * first artifact arrives.
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
import { ArrowDown, PanelRight } from "lucide-react";
import { Button } from "../ui/button";
import { ActivityDot } from "../ui/activity-dot";
import { ScrollArea } from "../ui/scroll-area";
import { cn } from "../lib/cn";
import { ChatMessage } from "./chat-message";
import { ArtifactPanel } from "./artifact-panel";
import { ChatContextIndicator } from "./chat-context-indicator";
import {
  getChatContextPresentation,
  type ChatContext,
} from "./chat-context";
import type {
  ChatArtifact,
  ChatSuggestion,
  ChatUIMessage,
} from "@repowise-dev/types/chat";
import type { SourceReference } from "./source-citations";
import { ChatComposer } from "./chat-composer";
import { ChatSuggestions } from "./chat-suggestions";
import { useChatScroll } from "./use-chat-scroll";

const DEFAULT_SUGGESTIONS: readonly ChatSuggestion[] = [
  "Give me an overview of this codebase",
  "Which files have the worst code health?",
  "Score the change risk of HEAD",
  "What dead code can be safely removed?",
  "What architectural decisions have been made?",
  "Search for authentication-related code",
].map((text) => ({ text, source: "static" as const }));

// Radix wraps viewport content in an inline `display: table` div that grows
// to its widest child, so one wide table or code line widened the whole
// transcript past a phone screen. As a block it holds the column width and
// wide content scrolls inside its own container.
const VIEWPORT_BLOCK = "[&>div]:block!";

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

  /** @deprecated Turns no longer render avatars. Ignored. */
  assistantAvatarSrc?: string;
  /** @deprecated Turns no longer render avatars. Ignored. */
  userAvatarSrc?: string;
  /** Forwarded to `SourceCitations` for href customisation. */
  buildCitationHref?: (source: SourceReference) => string;
  /** Forwarded to `SourceCitations` for route-agnostic link generation. */
  linkPrefix?: string;
  /** @deprecated The empty state no longer shows a logo. Ignored. */
  emptyStateLogoSrc?: string;
  /** Override the static tier with suggestions derived from live page data. */
  suggestions?: readonly ChatSuggestion[];
  /** Override the context-derived composer placeholder. */
  placeholder?: string;
  /** Orientation line under the empty-state subtitle — index status, page
   *  counts, branch, last sync. Keeps the blank page honest about what's
   *  loaded, and is the only figure on it, so it is not buried. */
  statusSlot?: ReactNode;
  /** Disables the composer (e.g. no chat provider configured). */
  sendDisabled?: boolean;
  /** Why sending is disabled, shown inside the composer. */
  sendDisabledReason?: ReactNode;
  /** Compact transcript treatment for a floating dock. */
  variant?: "page" | "dock";
  /** Optional controlled draft shared with another chat surface. */
  draft?: string;
  onDraftChange?: (draft: string) => void;
  onContextRemove?: () => void;
  composerRef?: RefObject<HTMLTextAreaElement | null>;
  /** Focus the composer on mount. The dock and the full page both do; a host
   *  embedding chat below other content should not. */
  autoFocus?: boolean;
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
  buildCitationHref,
  linkPrefix,
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
  autoFocus = false,
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
  const lastMessage = messages[messages.length - 1];
  // An errored turn never carries follow-ups: the server sends them only on a
  // turn that completed and called a tool.
  const followUps =
    !error && lastMessage?.role === "assistant" && !lastMessage.isStreaming
      ? lastMessage.followUps ?? []
      : [];
  // One pass rather than a backwards scan per turn on every streamed token.
  const modelChangedIds = useMemo(() => {
    const changed = new Set<string>();
    let previous: string | null = null;
    for (const m of messages) {
      if (m.role !== "assistant") continue;
      const identity = `${m.provider ?? ""}:${m.model ?? ""}`;
      if (previous !== null && previous !== identity) changed.add(m.id);
      previous = identity;
    }
    return changed;
  }, [messages]);
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

  function handleSuggestion(suggestion: ChatSuggestion) {
    setInput(suggestion.text);
    textareaRef.current?.focus();
  }

  const dock = variant === "dock";
  // One column for the transcript, the empty state and the composer.
  const column = cn("mx-auto w-full", dock ? "max-w-full px-4" : "max-w-[720px] px-[var(--page-pad)]");

  return (
    <div className="flex h-full min-h-0 flex-col" data-chat-variant={variant}>
      {/* Header controls. Always mounted at a fixed height so the transcript
          never shifts when the first artifact arrives. */}
      <div className={cn("flex h-11 shrink-0 items-center justify-between gap-2", dock ? "px-3" : "px-[var(--page-pad)]")}>
        <div className="flex min-w-0 items-center gap-2">{historySlot}</div>
        <Button
          variant="ghost"
          size="sm"
          aria-hidden={totalArtifactCount === 0 || undefined}
          tabIndex={totalArtifactCount === 0 ? -1 : undefined}
          className={cn(
            "h-8 shrink-0 gap-1.5 text-xs tabular-nums",
            totalArtifactCount === 0 && "invisible",
            artifactPulse && "text-[var(--color-text-primary)]",
          )}
          onClick={() => setArtifactPanelOpen(true)}
        >
          {artifactPulse ? (
            <ActivityDot className="h-1.5 w-1.5 bg-[var(--color-text-tertiary)]" />
          ) : null}
          <PanelRight className="h-4 w-4" />
          <span className="sr-only sm:not-sr-only">Artifacts</span>
          {totalArtifactCount}
        </Button>
      </div>

      {/* Message list or empty state */}
      <div className="flex-1 min-h-0 relative">
        {isEmpty ? (
          // Top-anchored rather than centred: vertical centring would need a
          // height Radix's `display:table` viewport wrapper does not pass down.
          <ScrollArea className="h-full" viewportRef={viewportRef} viewportClassName={VIEWPORT_BLOCK}>
            <div ref={contentRef} className={cn(column, "flex flex-col", dock ? "gap-5 py-6" : "gap-8 pb-8 pt-[12vh]")}>
              <div className="space-y-2">
                <h2 className={cn("font-semibold text-[var(--color-text-primary)]", dock ? "text-lg" : "text-[22px]")}>
                  {dock ? "Ask from this view" : `Ask anything about ${repoName ?? "this codebase"}`}
                </h2>
                <p className={cn("text-[var(--color-text-secondary)] leading-relaxed", dock ? "text-sm" : "text-base")}>
                  {dock
                    ? "Answers use the active repository context."
                    : "Architecture, risk, code health, decisions. Every answer cites the pages it read."}
                </p>
                {statusSlot && (
                  <p className="font-mono text-xs text-[var(--color-text-tertiary)] tabular-nums">
                    {statusSlot}
                  </p>
                )}
              </div>

              <ChatSuggestions
                suggestions={visibleSuggestions.slice(0, 4)}
                onSelect={handleSuggestion}
                layout="rows"
                ariaLabel="Suggested questions"
              />
            </div>
          </ScrollArea>
        ) : (
          <ScrollArea
            className="h-full"
            viewportRef={viewportRef}
            viewportClassName={cn(VIEWPORT_BLOCK, "[overflow-anchor:auto]")}
          >
            <div
              ref={contentRef}
              className={cn(column, dock ? "space-y-5 py-5" : "space-y-8 py-8")}
            >
              {messages.map((m, index) => {
                const isLast = index === messages.length - 1;
                return (
                <ChatMessage
                  key={m.id}
                  message={m}
                  repoId={repoId}
                  onViewArtifact={handleViewArtifact}
                  density={variant}
                  modelChanged={modelChangedIds.has(m.id)}
                  isLatest={isLast && m.role === "assistant"}
                  {...(isLast && m.role === "assistant" && error ? { error } : {})}
                  {...(onRetry ? { onRetry } : {})}
                  {...(onEditAndResend ? { onEditAndResend } : {})}
                  {...(buildCitationHref ? { buildCitationHref } : {})}
                  {...(linkPrefix ? { linkPrefix } : {})}
                />
                );
              })}
              {followUps.length > 0 && input.length === 0 && (
                /* Newest answer only, and only while the composer is empty: a
                   chip that overwrote a half-written question costs more than
                   it saves. */
                <ChatSuggestions
                  suggestions={followUps}
                  onSelect={handleSuggestion}
                  layout="chips"
                  ariaLabel="Next steps"
                />
              )}
              {error && lastMessage?.role !== "assistant" && (
                // The newest answer carries its own error and Retry; this is
                // only for a failure before any answer turn exists.
                <div role="alert" className="flex flex-wrap items-center gap-2 text-sm text-[var(--color-text-secondary)]">
                  <span aria-hidden className="h-1.5 w-1.5 shrink-0 rounded-full bg-[var(--color-error)]" />
                  <span className="min-w-0 [overflow-wrap:anywhere]">{error}</span>
                  {onRetry && lastMessage && (
                    <button
                      type="button"
                      onClick={() => void onRetry(lastMessage)}
                      className="inline-flex h-8 items-center rounded-md px-2 text-xs font-medium hover:bg-[var(--color-bg-elevated)] hover:text-[var(--color-text-primary)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent-primary)]"
                    >
                      Retry
                    </button>
                  )}
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
            aria-label="Jump to latest"
            title="Jump to latest"
            className="absolute bottom-3 left-1/2 z-10 flex h-8 w-8 -translate-x-1/2 items-center justify-center rounded-full border border-[var(--color-border-default)] bg-[var(--color-bg-overlay)] text-[var(--color-text-secondary)] shadow-[var(--shadow-sm)] hover:text-[var(--color-text-primary)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent-primary)]"
          >
            <ArrowDown className="h-4 w-4" />
          </button>
        )}
      </div>

      <span className="sr-only" role="status" aria-live="polite" aria-atomic="true">
        {announcement}
      </span>

      <div
        className={cn(
          "shrink-0",
          dock ? "pb-3 pt-1" : "pb-[max(1.25rem,env(safe-area-inset-bottom))] pt-2",
        )}
      >
        <div className={cn(column, dock && "px-3")}>
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
            {...(sendDisabledReason ? { disabledReason: sendDisabledReason } : {})}
            compact={dock}
            autoFocus={autoFocus}
            textareaRef={textareaRef}
            {...(modelSelectorSlot ? { footer: modelSelectorSlot } : {})}
          />
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
