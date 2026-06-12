"use client";

import { useEffect, useRef } from "react";
import useSWR from "swr";
import Link from "next/link";
import { ChatInterface as ChatInterfaceShell } from "@repowise-dev/ui/chat/chat-interface";
import { useChat } from "@/lib/hooks/use-chat";
import { pageHref } from "@/lib/utils/page-href";
import { listConversations } from "@/lib/api/chat";
import { getProviders } from "@/lib/api/providers";
import { getRepoStats } from "@/lib/api/repos";
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

  // Provider guard: surface "no chat provider configured" BEFORE the first
  // send instead of erroring after.
  const { data: providers } = useSWR(
    `providers:${repoId}`,
    () => getProviders(repoId),
    { revalidateOnFocus: false },
  );
  const anyConfigured =
    providers === undefined || providers.providers.some((p) => p.configured);

  // Orientation status line for the empty state.
  const { data: stats } = useSWR(
    `repo-stats:${repoId}`,
    () => getRepoStats(repoId),
    { revalidateOnFocus: false },
  );

  // Fire the seeded question exactly once (guards StrictMode double-effects).
  const seededRef = useRef(false);
  useEffect(() => {
    if (!initialQuestion || seededRef.current) return;
    seededRef.current = true;
    void sendMessage(initialQuestion);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [initialQuestion]);

  // Resume the most recent conversation when landing on a blank chat (no
  // seeded question). Once only; "New conversation" still resets cleanly.
  const resumedRef = useRef(false);
  useEffect(() => {
    if (resumedRef.current || seededRef.current || initialQuestion) return;
    if (messages.length > 0 || conversationId) return;
    resumedRef.current = true;
    void (async () => {
      try {
        const conversations = await listConversations(repoId);
        const latest = conversations[0];
        if (latest && latest.message_count > 0) {
          await loadConversation(latest.id);
        }
      } catch {
        // Resume is best-effort; a blank chat is a fine fallback.
      }
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [repoId]);

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
      statusSlot={
        stats ? (
          <span>
            {stats.file_count.toLocaleString()} files ·{" "}
            {Math.round(stats.doc_coverage_pct)}% documented
            {stats.symbol_count > 0 &&
              ` · ${stats.symbol_count.toLocaleString()} symbols indexed`}
          </span>
        ) : undefined
      }
      sendDisabled={!anyConfigured}
      sendDisabledReason={
        <span>
          No chat provider is configured. Add an API key in{" "}
          <Link
            href="/settings"
            className="text-[var(--color-accent-primary)] hover:underline"
          >
            settings
          </Link>{" "}
          to start asking questions.
        </span>
      }
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
