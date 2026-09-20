"use client";

import { useState } from "react";
import useSWR from "swr";
import { ConversationHistory as ConversationHistoryShell } from "@repowise-dev/ui/chat/conversation-history";
import {
  deleteConversation,
  forkConversation,
  listConversations,
  restoreConversation,
  updateConversation,
} from "@/lib/api/chat";
import type { ConversationResponse } from "@/lib/api/types";

interface Props {
  repoId: string;
  activeConversationId: string | null;
  onSelect: (id: string) => void;
  onNew: () => void;
  variant?: "popover" | "rail";
  collapsible?: boolean;
  railPreferenceKey?: string;
  className?: string;
}

export function ConversationHistoryWrapper({
  repoId,
  activeConversationId,
  onSelect,
  onNew,
  variant = "popover",
  collapsible = false,
  railPreferenceKey,
  className,
}: Props) {
  const [deleted, setDeleted] = useState<{ id: string; title: string } | null>(null);
  const { data: conversations, isLoading, mutate } = useSWR<
    ConversationResponse[]
  >(`chat-convs:${repoId}`, () => listConversations(repoId), {
    revalidateOnFocus: false,
  });

  async function handleDelete(convId: string) {
    const conversation = conversations?.find((item) => item.id === convId);
    await deleteConversation(repoId, convId);
    if (conversation) setDeleted({ id: convId, title: conversation.title });
    if (convId === activeConversationId) onNew();
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
      variant={variant}
      collapsible={collapsible}
      {...(railPreferenceKey ? { railPreferenceKey } : {})}
      className={className}
      onRename={async (id, title) => { await updateConversation(repoId, id, { title }); await mutate(); }}
      onPin={async (id, pinned) => { await updateConversation(repoId, id, { pinned }); await mutate(); }}
      onFork={async (id) => { const fork = await forkConversation(repoId, id); await mutate(); onSelect(fork.id); }}
      {...(deleted ? { undoDelete: {
        title: deleted.title,
        onUndo: async () => { await restoreConversation(repoId, deleted.id); setDeleted(null); await mutate(); },
      } } : {})}
    />
  );
}

// Backwards-compatible name for existing import sites.
export { ConversationHistoryWrapper as ConversationHistory };
