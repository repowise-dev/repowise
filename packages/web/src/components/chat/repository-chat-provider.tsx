"use client";

import {
  createContext,
  useCallback,
  useEffect,
  useContext,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import { usePathname, useSearchParams } from "next/navigation";
import type { ChatContext as PageChatContext } from "@repowise-dev/ui/chat";
import { useChat } from "@/lib/hooks/use-chat";
import { getRepositoryChatContext, getRepositoryChatContextQuery } from "./repository-chat-context";

type ChatController = ReturnType<typeof useChat>;

export interface RepositoryChatValue extends ChatController {
  repoId: string;
  repoName: string;
  pageContext: PageChatContext;
  selectedProvider: string | null;
  selectedModel: string | null;
  selectModel: (provider: string, model: string) => void;
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
      pageContext,
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
    }),
    [
      repoId,
      repoName,
      pageContext,
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
    ],
  );

  return (
    <RepositoryChatContext.Provider value={value}>
      {children}
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
