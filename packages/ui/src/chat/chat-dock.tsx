"use client";

import * as DialogPrimitive from "@radix-ui/react-dialog";
import {
  Expand,
  MessageCircle,
  Minus,
  X,
} from "lucide-react";
import { useEffect, useRef, useState, type CSSProperties, type ReactNode } from "react";
import type { ChatArtifact, ChatUIMessage } from "@repowise-dev/types/chat";
import { BrandMark } from "../shared/brand-mark";
import { Button } from "../ui/button";
import { ActivityDot } from "../ui/activity-dot";
import { cn } from "../lib/cn";
import type { SourceReference } from "./source-citations";
import { ChatComposer } from "./chat-composer";
import { ChatContextIndicator } from "./chat-context-indicator";
import { getChatContextPresentation, type ChatContext } from "./chat-context";
import { ChatInterface } from "./chat-interface";

export type ChatDockMode = "minimized" | "compact" | "expanded";

interface PersistedDockState {
  mode: ChatDockMode;
  draft: string;
}

const DEFAULT_STATE: PersistedDockState = { mode: "minimized", draft: "" };

function readDockState(storageKey: string): PersistedDockState {
  if (typeof window === "undefined") return DEFAULT_STATE;
  try {
    const value = JSON.parse(window.localStorage.getItem(storageKey) ?? "null") as
      | Partial<PersistedDockState>
      | null;
    const mode =
      value?.mode === "compact" || value?.mode === "expanded" || value?.mode === "minimized"
        ? value.mode
        : DEFAULT_STATE.mode;
    return { mode, draft: typeof value?.draft === "string" ? value.draft : "" };
  } catch {
    return DEFAULT_STATE;
  }
}

function usePersistentDockState(storageKey: string) {
  const [state, setState] = useState(() => ({
    storageKey,
    ...DEFAULT_STATE,
    hydrated: false,
  }));

  useEffect(() => {
    setState({ storageKey, ...readDockState(storageKey), hydrated: true });
  }, [storageKey]);

  useEffect(() => {
    if (
      !state.hydrated ||
      state.storageKey !== storageKey ||
      typeof window === "undefined"
    ) return;
    const timeout = window.setTimeout(() => {
      try {
        window.localStorage.setItem(
          storageKey,
          JSON.stringify({ mode: state.mode, draft: state.draft }),
        );
      } catch {
        // Storage can be unavailable in private or embedded contexts. The dock
        // remains fully usable for the current repository session.
      }
    }, 150);
    return () => window.clearTimeout(timeout);
  }, [state, storageKey]);

  const visible = state.storageKey === storageKey ? state : {
    storageKey,
    ...DEFAULT_STATE,
    hydrated: false,
  };
  return {
    mode: visible.mode,
    draft: visible.draft,
    setMode: (mode: ChatDockMode) =>
      setState((current) => ({
        ...(current.storageKey === storageKey ? current : readDockState(storageKey)),
        storageKey,
        mode,
        hydrated: true,
      })),
    setDraft: (draft: string) =>
      setState((current) => ({
        ...(current.storageKey === storageKey ? current : readDockState(storageKey)),
        storageKey,
        draft,
        hydrated: true,
      })),
  };
}

function useMobileDock() {
  const [isMobile, setIsMobile] = useState(true);

  useEffect(() => {
    if (typeof window.matchMedia !== "function") return;
    const query = window.matchMedia("(max-width: 767px)");
    const update = () => setIsMobile(query.matches);
    update();
    query.addEventListener?.("change", update);
    return () => query.removeEventListener?.("change", update);
  }, []);

  return isMobile;
}

export interface ChatDockProps {
  storageKey: string;
  repoId: string;
  repoName?: string;
  context: ChatContext;
  messages: ChatUIMessage[];
  isStreaming: boolean;
  error?: string | null;
  onSend: (text: string, context?: ChatContext) => void | Promise<void>;
  onCancel: () => void;
  suppressed?: boolean;
  /** Hide the dock entirely. The host owns the preference and where it is
   *  stored: this component's own persisted state is keyed per conversation,
   *  so a visibility choice kept there would reset on the next new chat.
   *  Omit to render no dismiss control at all. */
  onDismiss?: () => void;
  modelSelectorSlot?: ReactNode;
  historySlot?: ReactNode;
  sendDisabled?: boolean;
  sendDisabledReason?: ReactNode;
  assistantAvatarSrc?: string;
  buildCitationHref?: (source: SourceReference) => string;
  linkPrefix?: string;
  onOpenFullChat?: () => void;
  owlDarkSrc?: string;
  owlLightSrc?: string;
  /** Clears host page controls anchored near the viewport bottom. */
  collisionInset?: string;
  onArtifactPin?: (artifact: ChatArtifact, pinned: boolean) => void | Promise<void>;
  onOpenArtifactSource?: (artifact: ChatArtifact) => void;
  artifactOverrides?: Readonly<Record<string, ChatArtifact>>;
}

function contextIdentity(context: ChatContext) {
  return `${context.kind}:${context.label}:${context.target ?? ""}`;
}

/** Portable persistent dock. Hosts provide one existing chat controller. */
export function ChatDock({
  storageKey,
  repoId,
  repoName,
  context,
  messages,
  isStreaming,
  error,
  onSend,
  onCancel,
  suppressed = false,
  onDismiss,
  modelSelectorSlot,
  historySlot,
  sendDisabled = false,
  sendDisabledReason,
  assistantAvatarSrc,
  buildCitationHref,
  linkPrefix,
  onOpenFullChat,
  owlDarkSrc = "/repowise-logo.png",
  owlLightSrc = "/repowise-logo-light.png",
  collisionInset = "1rem",
  onArtifactPin,
  onOpenArtifactSource,
  artifactOverrides,
}: ChatDockProps) {
  const { mode, draft, setMode, setDraft } = usePersistentDockState(storageKey);
  const [dismissedContext, setDismissedContext] = useState<string | null>(null);
  const [answerReady, setAnswerReady] = useState(false);
  const previousStreaming = useRef(isStreaming);
  const compactTextareaRef = useRef<HTMLTextAreaElement>(null);
  const expandedTextareaRef = useRef<HTMLTextAreaElement>(null);
  const minimizedButtonRef = useRef<HTMLButtonElement>(null);
  const isMobile = useMobileDock();
  const identity = contextIdentity(context);
  const dockOffsetStyle = {
    "--chat-dock-bottom-offset": collisionInset,
  } as CSSProperties;
  const expandedDockStyle = {
    ...dockOffsetStyle,
    ...(isMobile
      ? {}
      : {
          height:
            "min(760px, calc(100dvh - var(--chat-dock-bottom-offset) - 1rem))",
        }),
  } as CSSProperties;

  useEffect(() => setDismissedContext(null), [identity]);

  useEffect(() => {
    if (previousStreaming.current && !isStreaming && mode === "minimized") {
      setAnswerReady(true);
      const timeout = window.setTimeout(() => setAnswerReady(false), 6000);
      previousStreaming.current = isStreaming;
      return () => window.clearTimeout(timeout);
    }
    previousStreaming.current = isStreaming;
    return undefined;
  }, [isStreaming, mode]);

  useEffect(() => {
    if (mode !== "minimized") setAnswerReady(false);
  }, [mode]);

  const activeContext = dismissedContext === identity ? undefined : context;
  const presentation = getChatContextPresentation(activeContext);
  const suggestion = presentation.suggestions[0];

  if (suppressed) return null;

  const send = (text: string) => onSend(text, activeContext);
  const removeCompactContext = () => {
    setDismissedContext(identity);
    window.requestAnimationFrame(() => compactTextareaRef.current?.focus());
  };
  const removeExpandedContext = () => {
    setDismissedContext(identity);
    window.requestAnimationFrame(() => expandedTextareaRef.current?.focus());
  };
  const minimize = () => {
    setMode("minimized");
    window.requestAnimationFrame(() => minimizedButtonRef.current?.focus());
  };
  const collapse = () => {
    setMode("compact");
    window.requestAnimationFrame(() => compactTextareaRef.current?.focus());
  };
  const statusText = isStreaming
    ? "Working"
    : answerReady
      ? error
        ? "Chat needs attention"
        : "Answer ready"
      : null;

  if (mode === "minimized") {
    return (
      <div
        style={dockOffsetStyle}
        className="group/dock fixed bottom-[max(var(--chat-dock-bottom-offset),env(safe-area-inset-bottom))] right-[max(1rem,env(safe-area-inset-right))] z-[calc(var(--z-toast)-1)]"
      >
        <button
          ref={minimizedButtonRef}
          type="button"
          onClick={() => setMode("compact")}
          aria-label={
            isStreaming
              ? "Open repository chat, response in progress"
              : answerReady
                ? `Open repository chat, ${error ? "chat needs attention" : "answer ready"}`
                : "Open repository chat"
          }
          className="group flex min-h-11 items-center gap-2 rounded-full border border-[var(--color-border-default)] bg-[var(--color-bg-overlay)] py-1.5 pl-1.5 pr-3 shadow-[var(--shadow-md)] outline-none transition-[border-color,box-shadow,transform] hover:border-[var(--color-border-hover)] hover:shadow-[var(--shadow-lg)] focus-visible:ring-2 focus-visible:ring-[var(--color-accent-primary)] motion-safe:hover:-translate-y-px motion-reduce:transition-none"
        >
          <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-[var(--color-bg-elevated)]">
            <BrandMark
              darkSrc={owlDarkSrc}
              lightSrc={owlLightSrc}
              size={25}
              alt=""
            />
          </span>
          <span className="text-[13px] font-medium text-[var(--color-text-primary)]">
            {statusText ?? "Ask Repowise"}
          </span>
          {statusText && (
            <span aria-hidden>
              {isStreaming ? (
                <ActivityDot className="block h-1.5 w-1.5 bg-[var(--color-text-tertiary)]" />
              ) : (
                <span className="block h-1.5 w-1.5 rounded-full bg-[var(--color-accent-secondary)]" />
              )}
            </span>
          )}
        </button>
        {onDismiss && (
          /* Quiet until wanted: the dismiss is the kind of control you look
             for only once you are already annoyed, and a permanent second
             button on the pill would make the thing louder to solve the
             complaint that it is too loud. Focusable while transparent, so
             the keyboard path is not gated on hover. */
          <button
            type="button"
            onClick={onDismiss}
            aria-label="Hide Ask Repowise"
            title="Hide Ask Repowise. Bring it back in Settings."
            className="absolute -right-1 -top-1 flex h-5 w-5 items-center justify-center rounded-full border border-[var(--color-border-default)] bg-[var(--color-bg-elevated)] text-[var(--color-text-tertiary)] opacity-0 shadow-[var(--shadow-md)] transition-opacity hover:text-[var(--color-text-primary)] focus-visible:opacity-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent-primary)] group-hover/dock:opacity-100 motion-reduce:transition-none"
          >
            <X className="h-3 w-3" />
          </button>
        )}
        <span className="sr-only" role="status" aria-live="polite">
          {statusText ?? ""}
        </span>
      </div>
    );
  }

  if (mode === "compact") {
    return (
      <aside
        style={dockOffsetStyle}
        aria-label="Repository chat"
        className="[--color-bg-inset:var(--color-bg-inset-on-overlay)] fixed inset-x-3 bottom-[max(var(--chat-dock-bottom-offset),env(safe-area-inset-bottom))] z-[calc(var(--z-toast)-1)] mx-auto w-auto max-w-[640px] rounded-2xl border border-[var(--color-border-default)] bg-[var(--color-bg-overlay)] px-3 pb-3 pt-2 shadow-[var(--shadow-lg)] sm:inset-x-auto sm:right-[max(1rem,env(safe-area-inset-right))] sm:w-[min(420px,calc(100vw-2rem))]"
      >
        <div className="flex min-w-0 items-center gap-1">
          <p className="min-w-0 flex-1 truncate pl-1 text-xs text-[var(--color-text-tertiary)]">
            {activeContext && activeContext.kind !== "repository"
              ? activeContext.label
              : repoName ?? "Repository"}
          </p>
          {isStreaming && (
            <span role="status" className="text-xs text-[var(--color-text-tertiary)]">
              Working
            </span>
          )}
          <Button
            variant="ghost"
            size="icon"
            className="h-8 w-8"
            onClick={() => setMode("expanded")}
            aria-label="Expand repository chat"
          >
            <Expand className="h-4 w-4" />
          </Button>
          <Button
            variant="ghost"
            size="icon"
            className="h-8 w-8"
            onClick={minimize}
            aria-label="Minimize repository chat"
          >
            <Minus className="h-4 w-4" />
          </Button>
          {onDismiss && (
            <Button
              variant="ghost"
              size="icon"
              className="h-8 w-8"
              onClick={onDismiss}
              aria-label="Hide Ask Repowise"
              title="Hide Ask Repowise. Bring it back in Settings."
            >
              <X className="h-4 w-4" />
            </Button>
          )}
        </div>
        {activeContext && activeContext.kind !== "repository" && (
          <ChatContextIndicator
            context={activeContext}
            onRemove={removeCompactContext}
            className="pb-1 pt-0.5"
          />
        )}
        <ChatComposer
          value={draft}
          onValueChange={setDraft}
          onSend={send}
          onCancel={onCancel}
          isStreaming={isStreaming}
          placeholder={presentation.placeholder}
          disabled={sendDisabled}
          compact
          appearance="bare"
          autoFocus
          textareaRef={compactTextareaRef}
        />
        {messages.length === 0 && draft.length === 0 && suggestion && (
          <button
            type="button"
            onClick={() => {
              setDraft(suggestion);
              compactTextareaRef.current?.focus();
            }}
            className="mt-2 block max-w-full truncate rounded-md px-1 py-1 text-left text-xs text-[var(--color-text-tertiary)] hover:text-[var(--color-text-primary)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent-primary)]"
          >
            <span className="mr-1.5 text-[var(--color-text-tertiary)]">Try</span>
            {suggestion}
          </button>
        )}
        {sendDisabled && sendDisabledReason && (
          <div className="mt-2 text-xs text-[var(--color-text-secondary)]">
            {sendDisabledReason}
          </div>
        )}
      </aside>
    );
  }

  return (
    <DialogPrimitive.Root
      open
      modal={isMobile}
      onOpenChange={(open) => {
        if (!open) collapse();
      }}
    >
      <DialogPrimitive.Portal>
        {isMobile && (
          <DialogPrimitive.Overlay className="fixed inset-0 z-[var(--z-modal)] bg-[color-mix(in_srgb,var(--color-bg-root)_72%,transparent)]" />
        )}
        <DialogPrimitive.Content
          style={expandedDockStyle}
          aria-describedby={undefined}
          onInteractOutside={(event) => {
            if (!isMobile) event.preventDefault();
          }}
          className="[--color-bg-inset:var(--color-bg-inset-on-overlay)] fixed inset-x-0 bottom-0 z-[var(--z-modal)] flex h-[min(88dvh,760px)] flex-col overflow-hidden rounded-t-2xl border-t border-[var(--color-border-default)] bg-[var(--color-bg-overlay)] pb-[env(safe-area-inset-bottom)] shadow-[var(--shadow-xl)] outline-none md:inset-x-auto md:bottom-[max(var(--chat-dock-bottom-offset),env(safe-area-inset-bottom))] md:right-[max(1rem,env(safe-area-inset-right))] md:w-[min(520px,calc(100vw-2rem))] md:rounded-2xl md:border xl:w-[min(580px,calc(100vw-2rem))]"
        >
          <DialogPrimitive.Title className="sr-only">
            Repository chat
          </DialogPrimitive.Title>
          <div className="flex justify-center py-2 md:hidden" aria-hidden>
            <div className="h-1 w-10 rounded-full bg-[var(--color-border-hover)]" />
          </div>
          <header className="flex min-w-0 items-center gap-2 border-b border-[var(--color-border-default)] px-3 py-2">
            <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-[var(--color-bg-elevated)]">
              <BrandMark darkSrc={owlDarkSrc} lightSrc={owlLightSrc} size={25} alt="" />
            </span>
            <div className="min-w-0 flex-1">
              <p className="truncate text-sm font-medium text-[var(--color-text-primary)]">
                Ask Repowise
              </p>
              <p className="truncate text-xs text-[var(--color-text-tertiary)]">
                {repoName ?? repoId}
              </p>
            </div>
            {onOpenFullChat && (
              <button
                type="button"
                onClick={onOpenFullChat}
                className="inline-flex h-8 items-center gap-1.5 rounded-md px-2 text-xs text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-elevated)] hover:text-[var(--color-text-primary)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent-primary)]"
              >
                <MessageCircle className="h-3.5 w-3.5" />
                Full chat
              </button>
            )}
            <Button
              variant="ghost"
              size="icon"
              className="h-8 w-8"
              onClick={collapse}
              aria-label="Collapse repository chat"
            >
              <Minus className="h-4 w-4" />
            </Button>
            <Button
              variant="ghost"
              size="icon"
              className="h-8 w-8"
              onClick={minimize}
              aria-label="Minimize repository chat"
            >
              <X className="h-4 w-4" />
            </Button>
          </header>
          <div className="min-h-0 flex-1">
            <ChatInterface
              variant="dock"
              repoId={repoId}
              {...(repoName ? { repoName } : {})}
              {...(activeContext ? { context: activeContext } : {})}
              messages={messages}
              isStreaming={isStreaming}
              {...(error !== undefined ? { error } : {})}
              onSend={send}
              onCancel={onCancel}
              draft={draft}
              onDraftChange={setDraft}
              onContextRemove={removeExpandedContext}
              composerRef={expandedTextareaRef}
              modelSelectorSlot={modelSelectorSlot}
              historySlot={historySlot}
              sendDisabled={sendDisabled}
              sendDisabledReason={sendDisabledReason}
              {...(assistantAvatarSrc ? { assistantAvatarSrc } : {})}
              {...(buildCitationHref ? { buildCitationHref } : {})}
              {...(linkPrefix ? { linkPrefix } : {})}
              {...(onArtifactPin ? { onArtifactPin } : {})}
              {...(onOpenArtifactSource ? { onOpenArtifactSource } : {})}
              {...(artifactOverrides ? { artifactOverrides } : {})}
            />
          </div>
        </DialogPrimitive.Content>
      </DialogPrimitive.Portal>
    </DialogPrimitive.Root>
  );
}
