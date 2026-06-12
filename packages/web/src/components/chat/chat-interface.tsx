"use client";

import { useEffect, useRef } from "react";
import { ChatInterface as ChatInterfaceShell } from "@repowise-dev/ui/chat/chat-interface";
import { useChat } from "@/lib/hooks/use-chat";
import { pageHref } from "@/lib/utils/page-href";
import { ModelSelector } from "./model-selector";
import { ConversationHistory } from "./conversation-history";

interface ChatInterfaceProps {
  repoId: string;
  repoName?: string;
  /** Question to send immediately on mount (quick-ask deep links, `?q=`). */
  initialQuestion?: string;
}

export function ChatInterface({ repoId, repoName, initialQuestion }: ChatInterfaceProps) {
  const {
    messages,
    conversationId,
    isStreaming,
    error,
    sendMessage,
    loadConversation,
    reset,
  } = useChat(repoId);

  // Fire the seeded question exactly once (guards StrictMode double-effects).
  const seededRef = useRef(false);
  useEffect(() => {
    if (!initialQuestion || seededRef.current) return;
    seededRef.current = true;
    void sendMessage(initialQuestion);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [initialQuestion]);

  return (
    <ChatInterfaceShell
      repoId={repoId}
      {...(repoName !== undefined ? { repoName } : {})}
      messages={messages}
      isStreaming={isStreaming}
      error={error}
      onSend={(text) => sendMessage(text)}
      onCancel={reset}
      buildCitationHref={(s) => pageHref(repoId, s.pageId)}
      modelSelectorSlot={<ModelSelector repoId={repoId} />}
      historySlot={
        <ConversationHistory
          repoId={repoId}
          activeConversationId={conversationId}
          onSelect={loadConversation}
          onNew={reset}
        />
      }
    />
  );
}
