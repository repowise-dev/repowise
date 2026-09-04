"use client";

import useSWR from "swr";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { ChatDock, getArtifactSourceTarget } from "@repowise-dev/ui/chat";
import { getProviders } from "@/lib/api/providers";
import { setConversationArtifactPinned } from "@/lib/api/chat";
import { pageHref } from "@/lib/utils/page-href";
import { ModelSelector } from "./model-selector";
import { ConversationHistory } from "./conversation-history";
import { useRepositoryChat } from "./repository-chat-provider";
import { useChatDockHidden } from "./use-chat-dock-hidden";
import { setChatDockHidden } from "@/lib/config";

// Sigma's worst-case bottom-right stack is ~197px tall (layout status plus
// five controls and spacing). Keep a small measured clearance above it.
export const REPOSITORY_GRAPH_DOCK_INSET = "12.5rem";

/** Architecture `?view=` values that render a table, not the Sigma canvas.
 *  `deps` is the legacy spelling of `packages`. Absent or unrecognised means
 *  the Map tab, which is the default landing view. */
const ARCHITECTURE_VIEWS_WITHOUT_CANVAS = new Set([
  "coupling",
  "packages",
  "symbols",
  "deps",
]);

/**
 * Lift the dock only where something is actually beneath it.
 *
 * The clearance exists for exactly one element: Sigma's zoom/fit/layout stack,
 * anchored bottom-right on Architecture's Map tab. It used to key off the chat
 * context kind alone, which raised the pill 200px on two surfaces with nothing
 * under it — the Knowledge Graph, whose only bottom-anchored control sits
 * bottom-LEFT, and Architecture's Coupling, Third-party and Symbols tabs,
 * which render tables and mount no canvas at all.
 */
export function getRepositoryDockCollisionInset(kind: string, view?: string) {
  if (kind !== "architecture") return undefined;
  if (view && ARCHITECTURE_VIEWS_WITHOUT_CANVAS.has(view)) return undefined;
  return REPOSITORY_GRAPH_DOCK_INSET;
}

export function RepositoryChatDock() {
  const chat = useRepositoryChat();
  const hidden = useChatDockHidden();

  // The full chat page already renders this controller's complete transcript.
  // Keep the dock out of both the visual and accessibility trees there.
  if (chat.pageContext.kind === "chat") return null;

  // Bail before the connected component rather than passing `suppressed`: that
  // prop returns null from inside ChatDock, which means the provider fetch and
  // the dock's own state still run for something nobody can see.
  if (hidden) return null;

  return <ConnectedRepositoryChatDock chat={chat} />;
}

type RepositoryChatValue = ReturnType<typeof useRepositoryChat>;

function ConnectedRepositoryChatDock({ chat }: { chat: RepositoryChatValue }) {
  const router = useRouter();
  const searchParams = useSearchParams();
  const { data: providers } = useSWR(
    `providers:${chat.repoId}`,
    () => getProviders(chat.repoId),
    { revalidateOnFocus: false },
  );
  const anyConfigured =
    providers === undefined || providers.providers.some((provider) => provider.configured);
  const collisionInset = getRepositoryDockCollisionInset(
    chat.pageContext.kind,
    searchParams.get("view") ?? undefined,
  );

  return (
    <ChatDock
      storageKey={`repowise:chat-dock:${chat.repoId}:${chat.conversationId ?? "new"}`}
      repoId={chat.repoId}
      repoName={chat.repoName}
      context={chat.pageContext}
      messages={chat.messages}
      isStreaming={chat.isStreaming}
      error={chat.error}
      onSend={(text, context) => chat.sendMessage(text, {
        context,
        ...(chat.selectedProvider ? { provider: chat.selectedProvider } : {}),
        ...(chat.selectedModel ? { model: chat.selectedModel } : {}),
      })}
      onCancel={chat.cancel}
      buildCitationHref={(source) => pageHref(chat.repoId, source.pageId)}
      onArtifactPin={async (artifact, pinned) => {
        if (!chat.conversationId) return;
        chat.replaceArtifact(await setConversationArtifactPinned(chat.repoId, chat.conversationId, artifact.id, pinned));
      }}
      artifactOverrides={chat.artifactOverrides}
      onOpenArtifactSource={(artifact) => {
        const target = getArtifactSourceTarget(artifact);
        if (target?.pageId) router.push(pageHref(chat.repoId, target.pageId));
        else if (target?.path) router.push(pageHref(chat.repoId, `file_page:${target.path}`));
      }}
      onOpenFullChat={() => router.push(
        chat.conversationId
          ? `/repos/${chat.repoId}/chat?conversation=${encodeURIComponent(chat.conversationId)}`
          : `/repos/${chat.repoId}/chat`,
      )}
      {...(collisionInset ? { collisionInset } : {})}
      onDismiss={() => setChatDockHidden(true)}
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
      modelSelectorSlot={<ModelSelector repoId={chat.repoId} activeProvider={chat.selectedProvider} activeModel={chat.selectedModel} onSelect={chat.selectModel} />}
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
