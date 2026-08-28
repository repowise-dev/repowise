"use client";

import {
  createContext,
  useContext,
  useMemo,
  type ReactNode,
} from "react";
import { usePathname, useSearchParams } from "next/navigation";
import type { ChatContext as PageChatContext } from "@repowise-dev/ui/chat";
import { useChat } from "@/lib/hooks/use-chat";
import { getRepositoryChatContext } from "./repository-chat-context";

type ChatController = ReturnType<typeof useChat>;

export interface RepositoryChatValue extends ChatController {
  repoId: string;
  repoName: string;
  pageContext: PageChatContext;
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
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const pageContext = useMemo(
    () => getRepositoryChatContext(pathname, searchParams),
    [pathname, searchParams],
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
  } = chat;
  const value = useMemo<RepositoryChatValue>(
    () => ({
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
