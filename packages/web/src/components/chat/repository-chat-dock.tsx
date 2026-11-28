"use client";

import useSWR from "swr";
import { useRouter, useSearchParams } from "next/navigation";
import { useEffect, useState } from "react";
import { CHAT_SHORTCUT_HINT, ChatDock, getArtifactSourceTarget } from "@repowise-dev/ui/chat";
import { getProviders } from "@/lib/api/providers";
import { setConversationArtifactPinned } from "@/lib/api/chat";
import { pageHref } from "@/lib/utils/page-href";
import { ModelSelector } from "./model-selector";
import { ConversationHistory } from "./conversation-history";
import { useRepositoryChat } from "./repository-chat-provider";
import { useChatDockHidden } from "./use-chat-dock-hidden";
import { config, setChatDockHidden } from "@/lib/config";
import { useTranslations } from "next-intl";
import { NoProviderReason } from "./no-provider-reason";
import { useModelUpgrade } from "@/components/model-upgrade/model-upgrade";

/** Architecture `?view=` values that render a table, not the Sigma canvas.
 *  `deps` is the legacy spelling of `packages`. Absent or unrecognised means
 *  the Map tab, which is the default landing view. `tree` is the same file
 *  graph as an outline: no canvas, so nothing in the bottom-right corner. */
const ARCHITECTURE_VIEWS_WITHOUT_CANVAS = new Set([
  "coupling",
  "packages",
  "symbols",
  "deps",
  "tree",
]);

/**
 * Hide the minimized pill on Architecture's Map canvas only.
 *
 * Sigma anchors its zoom/fit/layout stack bottom-right there, and its height
 * above the viewport bottom moves with the legend footer, so no fixed lift
 * clears it. The breadcrumb's Ask link and the chat shortcut stay as the ways
 * in, and an opened dock still renders as usual. Every other page keeps the
 * pill.
 */
export function hidesRepositoryDockPill(kind: string, view?: string) {
  if (kind !== "architecture") return false;
  return !(view && ARCHITECTURE_VIEWS_WITHOUT_CANVAS.has(view));
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
  const t = useTranslations("chat");
  const router = useRouter();
  const searchParams = useSearchParams();
  // Read after mount so SSR and the first client render agree.
  const [introduce, setIntroduce] = useState(false);
  useEffect(() => setIntroduce(!config.getChatHintSeen()), []);
  const { data: providers } = useSWR(
    `providers:${chat.repoId}`,
    () => getProviders(chat.repoId),
    { revalidateOnFocus: false },
  );
  // Outside the composer's reason, which unmounts once a provider is set.
  const upgrade = useModelUpgrade(chat.repoId);
  const anyConfigured =
    providers === undefined || providers.providers.some((provider) => provider.configured);
  const hidePill = hidesRepositoryDockPill(
    chat.pageContext.kind,
    searchParams.get("view") ?? undefined,
  );

  return (
    <>
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
      {...(chat.suggestions ? { suggestions: chat.suggestions } : {})}
      command={chat.dockCommand}
      onCommandHandled={chat.clearDockCommand}
      {...(introduce ? { firstVisitHint: CHAT_SHORTCUT_HINT } : {})}
      onFirstVisitHintShown={() => config.setChatHintSeen()}
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
      hidePill={hidePill}
      onDismiss={() => setChatDockHidden(true)}
      unverifiedFigureCopy={{ flag: t("unverifiedFigure"), note: t("unverifiedFiguresNote") }}
      sendDisabled={!anyConfigured}
      sendDisabledReason={<NoProviderReason message="noProviderShort" onAddModel={upgrade.openSheet} />}
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
    {upgrade.sheet}
    </>
  );
}
