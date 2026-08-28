"use client";

import useSWR from "swr";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { ChatDock } from "@repowise-dev/ui/chat";
import { getProviders } from "@/lib/api/providers";
import { pageHref } from "@/lib/utils/page-href";
import { ModelSelector } from "./model-selector";
import { ConversationHistory } from "./conversation-history";
import { useRepositoryChat } from "./repository-chat-provider";

// Sigma's worst-case bottom-right stack is ~197px tall (layout status plus
// five controls and spacing). Keep a small measured clearance above it.
export const REPOSITORY_GRAPH_DOCK_INSET = "12.5rem";

export function getRepositoryDockCollisionInset(kind: string) {
  return kind === "architecture" || kind === "graph"
    ? REPOSITORY_GRAPH_DOCK_INSET
    : undefined;
}

export function RepositoryChatDock() {
  const chat = useRepositoryChat();

  // The full chat page already renders this controller's complete transcript.
  // Keep the dock out of both the visual and accessibility trees there.
  if (chat.pageContext.kind === "chat") return null;

  return <ConnectedRepositoryChatDock chat={chat} />;
}

type RepositoryChatValue = ReturnType<typeof useRepositoryChat>;

function ConnectedRepositoryChatDock({ chat }: { chat: RepositoryChatValue }) {
  const router = useRouter();
  const { data: providers } = useSWR(
    `providers:${chat.repoId}`,
    () => getProviders(chat.repoId),
    { revalidateOnFocus: false },
  );
  const anyConfigured =
    providers === undefined || providers.providers.some((provider) => provider.configured);
  const collisionInset = getRepositoryDockCollisionInset(chat.pageContext.kind);

  return (
    <ChatDock
      storageKey={`repowise:chat-dock:${chat.repoId}`}
      repoId={chat.repoId}
      repoName={chat.repoName}
      context={chat.pageContext}
      messages={chat.messages}
      isStreaming={chat.isStreaming}
      error={chat.error}
      onSend={(text, context) => chat.sendMessage(text, { context })}
      onCancel={chat.cancel}
      buildCitationHref={(source) => pageHref(chat.repoId, source.pageId)}
      onOpenFullChat={() => router.push(`/repos/${chat.repoId}/chat`)}
      {...(collisionInset ? { collisionInset } : {})}
      sendDisabled={!anyConfigured}
      sendDisabledReason={
        <span>
          No chat provider is configured. Add an API key in{" "}
          <Link
            href="/settings"
            className="text-[var(--color-accent-primary)] hover:underline"
          >
            settings
          </Link>
          .
        </span>
      }
      modelSelectorSlot={<ModelSelector repoId={chat.repoId} />}
      historySlot={
        <ConversationHistory
          repoId={chat.repoId}
          activeConversationId={chat.conversationId}
          onSelect={chat.loadConversation}
          onNew={chat.reset}
        />
      }
    />
  );
}
