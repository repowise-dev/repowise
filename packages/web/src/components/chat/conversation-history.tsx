"use client";

import { useState } from "react";
import useSWR from "swr";
import { History, Plus, Trash2 } from "lucide-react";
import { cn } from "@/lib/utils/cn";
import { listConversations, deleteConversation } from "@/lib/api/chat";
import type { ConversationResponse } from "@/lib/api/types";

interface ConversationHistoryProps {
  repoId: string;
  activeConversationId: string | null;
  onSelect: (id: string) => void;
  onNew: () => void;
}

function formatRelativeTime(dateStr: string): string {
  const diff = Date.now() - new Date(dateStr).getTime();
  const mins = Math.floor(diff / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  if (days < 7) return `${days}d ago`;
  return new Date(dateStr).toLocaleDateString();
}

export function ConversationHistory({
  repoId,
  activeConversationId,
  onSelect,
  onNew,
}: ConversationHistoryProps) {
  const [open, setOpen] = useState(false);
  const { data: conversations, mutate } = useSWR<ConversationResponse[]>(
    open ? `chat-convs:${repoId}` : null,
    () => listConversations(repoId),
    { revalidateOnFocus: false },
  );

  async function handleDelete(convId: string) {
    await deleteConversation(repoId, convId);
    await mutate();
  }

  return (
    <div className="relative">
      <button
        onClick={() => setOpen(!open)}
        className={cn(
          "flex items-center gap-1 rounded-md px-2 py-1 text-xs",
          "text-[var(--color-text-secondary)] hover:text-[var(--color-text-primary)]",
          "hover:bg-[var(--color-bg-elevated)] transition-colors",
        )}
      >
        <History className="h-3.5 w-3.5" />
        History
      </button>

      {open && (
        <>
          <div
            className="fixed inset-0 z-[var(--z-dropdown)]"
            onClick={() => setOpen(false)}
          />
          <div className="absolute left-0 top-full mt-1 z-[calc(var(--z-dropdown)+1)] w-72 rounded-lg border border-[var(--color-border-default)] bg-[var(--color-bg-overlay)] shadow-lg overflow-hidden">
            {/* New conversation */}
            <button
              onClick={() => {
                onNew();
                setOpen(false);
              }}
              className="flex w-full items-center gap-2 px-3 py-2 text-xs text-[var(--color-accent-primary)] hover:bg-[var(--color-bg-elevated)] transition-colors border-b border-[var(--color-border-default)]"
            >
              <Plus className="h-3.5 w-3.5" />
              New conversation
            </button>

            {/* Conversation list */}
            <div className="max-h-64 overflow-y-auto">
              {!conversations && (
                <div className="px-3 py-4 text-xs text-[var(--color-text-tertiary)] text-center">
                  Loading...
                </div>
              )}
              {conversations?.length === 0 && (
                <div className="px-3 py-4 text-xs text-[var(--color-text-tertiary)] text-center">
                  No conversations yet
                </div>
              )}
              {conversations?.map((conv) => (
                <div
                  key={conv.id}
                  className={cn(
                    "flex items-center gap-2 px-3 py-2 hover:bg-[var(--color-bg-elevated)] transition-colors group cursor-pointer",
                    conv.id === activeConversationId &&
                      "bg-[var(--color-accent-muted)]",
                  )}
                  onClick={() => {
                    onSelect(conv.id);
                    setOpen(false);
                  }}
                >
                  <div className="flex-1 min-w-0">
                    <div className="text-xs text-[var(--color-text-primary)] truncate">
                      {conv.title}
                    </div>
                    <div className="text-[10px] text-[var(--color-text-tertiary)]">
                      {formatRelativeTime(conv.updated_at)} ·{" "}
                      {conv.message_count} msgs
                    </div>
                  </div>
                  <button
                    onClick={(e) => {
                      e.stopPropagation();
                      handleDelete(conv.id);
                    }}
                    className="opacity-0 group-hover:opacity-100 p-1 text-[var(--color-text-tertiary)] hover:text-red-400 transition-all"
                  >
                    <Trash2 className="h-3 w-3" />
                  </button>
                </div>
              ))}
            </div>
          </div>
        </>
      )}
    </div>
  );
}
