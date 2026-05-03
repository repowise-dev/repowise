"use client";

import { ChatInterface as ChatInterfaceShell } from "@repowise-dev/ui/chat/chat-interface";
import { useChat } from "@/lib/hooks/use-chat";
import { ModelSelector } from "./model-selector";
import { ConversationHistory } from "./conversation-history";

interface ChatInterfaceProps {
  repoId: string;
  repoName?: string;
}

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

  return (
    <ChatInterfaceShell
      repoId={repoId}
      {...(repoName !== undefined ? { repoName } : {})}
      messages={messages}
      isStreaming={isStreaming}
      error={error}
      onSend={(text) => sendMessage(text)}
      onCancel={reset}
      modelSelectorSlot={<ModelSelector />}
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
