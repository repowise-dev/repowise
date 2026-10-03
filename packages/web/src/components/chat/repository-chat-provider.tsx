"use client";

import {
  createContext,
  useCallback,
  useEffect,
  useContext,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { usePathname, useSearchParams } from "next/navigation";
import useSWR from "swr";
import {
  ChatHandoffProvider,
  ChatSelectionAffordance,
  useChatShortcut,
  type ChatContext as PageChatContext,
  type ChatDockCommand,
  type ChatHandoff,
} from "@repowise-dev/ui/chat";
import type { ChatSuggestion } from "@repowise-dev/types/chat";
import { getChatSuggestions } from "@/lib/api/chat";
import { useChat } from "@/lib/hooks/use-chat";
import { setChatDockHidden } from "@/lib/config";
import { getRepositoryChatContext, getRepositoryChatContextQuery } from "./repository-chat-context";
import { useChatAffordances } from "./use-chat-dock-hidden";

type ChatController = ReturnType<typeof useChat>;

export interface RepositoryChatValue extends ChatController {
  repoId: string;
  repoName: string;
  /** The page context, or what a handoff replaced it with on this route. */
  pageContext: PageChatContext;
  selectedProvider: string | null;
  selectedModel: string | null;
  selectModel: (provider: string, model: string) => void;
  /** One pending imperative request for the dock. */
  dockCommand: ChatDockCommand | null;
  clearDockCommand: () => void;
  /** Questions derived from this page's own data, or undefined while the
   *  request is in flight and whenever the page has nothing measured. */
  suggestions: ChatSuggestion[] | undefined;
}

const RepositoryChatContext = createContext<RepositoryChatValue | null>(null);

export function RepositoryChatProvider({
  repoId,
  repoName,
  children,
}: {
  repoId: string;
  repoName: string;
  children: ReactNode;
}) {
  const chat = useChat(repoId);
  const [modelSelection, setModelSelection] = useState<{ provider: string; model: string } | null>(null);
  const modelIdentity = chat.conversationId ?? "new";
  useEffect(() => {
    const storageKey = `repowise:chat-model:${repoId}:${modelIdentity}`;
    try {
      const stored = JSON.parse(window.localStorage.getItem(storageKey) ?? "null") as typeof modelSelection;
      if (stored?.provider && stored.model) setModelSelection(stored);
      else {
        const lastAssistant = [...chat.messages].reverse().find((message) => message.role === "assistant" && message.provider && message.model);
        setModelSelection(lastAssistant?.provider && lastAssistant.model ? { provider: lastAssistant.provider, model: lastAssistant.model } : null);
      }
    } catch {
      setModelSelection(null);
    }
  // Deliberately keyed to conversation identity, not streaming message updates.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [modelIdentity, repoId]);

  const selectModel = useCallback((provider: string, model: string) => {
    const selection = { provider, model };
    setModelSelection(selection);
    try {
      window.localStorage.setItem(`repowise:chat-model:${repoId}:${modelIdentity}`, JSON.stringify(selection));
    } catch {}
  }, [modelIdentity, repoId]);
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const contextQuery = getRepositoryChatContextQuery(searchParams);
  const pageContext = useMemo(
    () => getRepositoryChatContext(pathname, new URLSearchParams(contextQuery)),
    [contextQuery, pathname],
  );
  const { dockHidden, askControlsEnabled, selectionAskEnabled } =
    useChatAffordances();
  const [dockCommand, setDockCommand] = useState<ChatDockCommand | null>(null);
  const commandId = useRef(0);
  // A handoff replaces the route's context until the reader navigates away.
  // Keyed on the same identity `pageContext` is derived from, query included:
  // picking a different `?page=` or `?file=` changes what the page is about
  // without changing the pathname, and the handoff must not outlive that.
  // Stored alongside that identity rather than cleared by an effect, so there
  // is no render where the two disagree.
  const routeIdentity = `${pathname}?${contextQuery}`;
  const [handoffContext, setHandoffContext] = useState<
    { routeIdentity: string; context: PageChatContext } | null
  >(null);

  const issue = useCallback((command: Omit<ChatDockCommand, "id">) => {
    commandId.current += 1;
    setDockCommand({ ...command, id: commandId.current });
  }, []);

  const clearDockCommand = useCallback(() => setDockCommand(null), []);

  const requestHandoff = useCallback(
    (handoff: ChatHandoff) => {
      // A deliberate gesture outranks the stored preference: opening chat from
      // a page must not silently do nothing because the pill was dismissed.
      setChatDockHidden(false);
      setHandoffContext({ routeIdentity, context: handoff.context });
      issue({ type: "handoff", handoff });
    },
    [issue, routeIdentity],
  );

  useChatShortcut(
    useCallback(() => {
      // A deliberate keystroke outranks the stored preference.
      if (dockHidden) {
        setChatDockHidden(false);
        issue({ type: "open" });
        return;
      }
      issue({ type: "toggle" });
    }, [dockHidden, issue]),
  );

  const activeContext =
    handoffContext?.routeIdentity === routeIdentity
      ? handoffContext.context
      : pageContext;

  // Without a target the server has nothing to prefetch and would answer with
  // the static tier the chips already fall back to, so that request is skipped
  // rather than made and discarded.
  const suggestionKey = activeContext.target
    ? `chat-suggestions:${repoId}:${activeContext.kind}:${activeContext.target}`
    : null;
  const { data: suggestionData } = useSWR(
    suggestionKey,
    () =>
      getChatSuggestions(repoId, {
        kind: activeContext.kind,
        ...(activeContext.target ? { target: activeContext.target } : {}),
      }),
    // The key already isolates each page. Stated anyway so a future global
    // default cannot start offering the previous file's questions.
    { keepPreviousData: false },
  );
  // An empty list means the page measured nothing, which is the static tier's
  // cue, not an instruction to show no chips at all.
  const suggestions = suggestionData?.suggestions?.length
    ? suggestionData.suggestions
    : undefined;

  // The full chat page mounts no dock, so a handoff made there would lead
  // nowhere. Its transcript already carries its own follow-up affordance.
  const onChatPage = pageContext.kind === "chat";

  /** Selections name a file; everything else stays on the page's own context. */
  const resolveSelectionContext = useCallback(
    (path?: string): PageChatContext =>
      path
        ? { kind: "file", label: path, target: path, targetKind: "path" }
        : activeContext,
    [activeContext],
  );

  const {
    messages,
    conversationId,
    isStreaming,
    error,
    sendMessage,
    loadConversation,
    cancel,
    reset,
    artifactOverrides,
    replaceArtifact,
  } = chat;
  const value = useMemo<RepositoryChatValue>(
    () => ({
      repoId,
      repoName,
      pageContext: activeContext,
      selectedProvider: modelSelection?.provider ?? null,
      selectedModel: modelSelection?.model ?? null,
      selectModel,
      messages,
      conversationId,
      isStreaming,
      error,
      sendMessage,
      loadConversation,
      cancel,
      reset,
      artifactOverrides,
      replaceArtifact,
      dockCommand,
      clearDockCommand,
      suggestions,
    }),
    [
      repoId,
      repoName,
      activeContext,
      messages,
      conversationId,
      isStreaming,
      error,
      sendMessage,
      loadConversation,
      cancel,
      reset,
      artifactOverrides,
      replaceArtifact,
      modelSelection,
      selectModel,
      dockCommand,
      clearDockCommand,
      suggestions,
    ],
  );

  return (
    <RepositoryChatContext.Provider value={value}>
      <ChatHandoffProvider
        onHandoff={requestHandoff}
        askEnabled={askControlsEnabled && !onChatPage}
        selectionEnabled={selectionAskEnabled && !onChatPage}
      >
        {children}
        <ChatSelectionAffordance resolveContext={resolveSelectionContext} />
      </ChatHandoffProvider>
    </RepositoryChatContext.Provider>
  );
}

export function useRepositoryChat(): RepositoryChatValue {
  const value = useContext(RepositoryChatContext);
  if (!value) {
    throw new Error("useRepositoryChat must be used within RepositoryChatProvider");
  }
  return value;
}
