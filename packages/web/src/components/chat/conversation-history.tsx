"use client";

import useSWR from "swr";
import { ConversationHistory as ConversationHistoryShell } from "@repowise-dev/ui/chat/conversation-history";
import { listConversations, deleteConversation } from "@/lib/api/chat";
import type { ConversationResponse } from "@/lib/api/types";

interface Props {
  repoId: string;
  activeConversationId: string | null;
  onSelect: (id: string) => void;
  onNew: () => void;
}

export function ConversationHistoryWrapper({
  repoId,
  activeConversationId,
  onSelect,
  onNew,
}: Props) {
  const { data: conversations, isLoading, mutate } = useSWR<
    ConversationResponse[]
  >(`chat-convs:${repoId}`, () => listConversations(repoId), {
    revalidateOnFocus: false,
  });

  async function handleDelete(convId: string) {
    await deleteConversation(repoId, convId);
    await mutate();
  }

  return (
    <ConversationHistoryShell
      conversations={conversations}
      isLoading={isLoading}
      selectedId={activeConversationId}
      onSelect={onSelect}
      onDelete={handleDelete}
      onNew={onNew}
    />
  );
}

// Backwards-compatible name for existing import sites.
export { ConversationHistoryWrapper as ConversationHistory };
