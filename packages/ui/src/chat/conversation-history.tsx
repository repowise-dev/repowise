"use client";

/**
 * Presentational shell for the chat conversation-history dropdown. The data
 * fetch (SWR list) and delete mutation live in the consumer wrapper; this
 * shell only renders the trigger, popover surface, list rows, and emits
 * select / delete / new intents via callbacks.
 */

import { useState } from "react";
import { History, Plus, Trash2 } from "lucide-react";
import { cn } from "../lib/cn";
import type { Conversation } from "@repowise-dev/types/chat";

export interface ConversationHistoryProps {
  conversations: Conversation[] | undefined;
  isLoading?: boolean;
  /** id of the currently active conversation. */
  selectedId?: string | null;
  onSelect: (id: string) => void;
  onDelete: (id: string) => void | Promise<void>;
  onNew: () => void;
  className?: string;
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
  conversations,
  isLoading = false,
  selectedId = null,
  onSelect,
  onDelete,
  onNew,
  className,
}: ConversationHistoryProps) {
  const [open, setOpen] = useState(false);

  return (
    <div className={cn("relative", className)}>
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

            <div className="max-h-64 overflow-y-auto">
              {isLoading && !conversations && (
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
                    conv.id === selectedId &&
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
                      void onDelete(conv.id);
                    }}
                    className="opacity-0 group-hover:opacity-100 p-1 text-[var(--color-text-tertiary)] hover:text-red-400 transition-all"
                    aria-label="Delete conversation"
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
