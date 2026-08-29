"use client";

import React, { useCallback, useEffect, useRef } from "react";
import useSWR from "swr";
import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { ChatInterface as ChatInterfaceShell } from "@repowise-dev/ui/chat/chat-interface";
import { getArtifactSourceTarget, useChatDraft } from "@repowise-dev/ui/chat";
import { pageHref } from "@/lib/utils/page-href";
import { getProviders } from "@/lib/api/providers";
import { getRepoStats } from "@/lib/api/repos";
import { forkConversation, setConversationArtifactPinned } from "@/lib/api/chat";
import type { ChatArtifact, ChatUIMessage } from "@repowise-dev/types/chat";
import { ModelSelector } from "./model-selector";
import { ConversationHistory } from "./conversation-history";
import { useRepositoryChat } from "./repository-chat-provider";

interface ChatInterfaceProps {
  repoId: string;
  repoName?: string;
  /** Branch shown in the empty-state status line. */
  defaultBranch?: string;
  /** HEAD SHA shown in the empty-state status line, abbreviated to 7. */
  headCommit?: string;
  /** Question to send immediately on mount (quick-ask deep links, `?q=`). */
  initialQuestion?: string;
}

export function ChatInterface({
  repoId,
  repoName,
  defaultBranch,
  headCommit,
  initialQuestion,
}: ChatInterfaceProps) {
  const {
    messages,
    conversationId,
    isStreaming,
    error,
    sendMessage,
    loadConversation,
    cancel,
    reset,
    pageContext,
    selectedProvider,
    selectedModel,
    selectModel,
    artifactOverrides,
    replaceArtifact,
  } = useRepositoryChat();
  const pathname = usePathname();
  const router = useRouter();
  const searchParams = useSearchParams();
  const searchParamsRef = useRef(searchParams);
  searchParamsRef.current = searchParams;
  const urlConversationId = searchParams.get("conversation");
  const activeArtifactId = searchParams.get("artifact");
  const compareArtifactId = searchParams.get("compare");
  const [draft, setDraft] = useChatDraft(
    `repowise:chat-draft:${repoId}:${urlConversationId ?? conversationId ?? "new"}`,
  );

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

  // A plain /chat URL is an explicit fresh workspace. Only an addressable
  // conversation URL restores history, which keeps returning users from being
  // dropped at the bottom of an old answer they did not choose to reopen.
  const lifecycleRef = useRef("");
  const awaitingFreshIdRef = useRef(false);
  useEffect(() => {
    const identity = `${repoId}:${urlConversationId ?? "new"}:${initialQuestion ?? ""}`;
    if (lifecycleRef.current === identity) return;
    lifecycleRef.current = identity;
    if (urlConversationId) {
      awaitingFreshIdRef.current = false;
      if (urlConversationId !== conversationId) {
        void loadConversation(urlConversationId);
      }
      return;
    }
    awaitingFreshIdRef.current = true;
    reset();
    if (initialQuestion) void sendMessage(initialQuestion, {
      context: pageContext,
      ...(selectedProvider ? { provider: selectedProvider } : {}),
      ...(selectedModel ? { model: selectedModel } : {}),
    });
  }, [conversationId, initialQuestion, loadConversation, pageContext, repoId, reset, selectedModel, selectedProvider, sendMessage, urlConversationId]);

  useEffect(() => {
    if (!awaitingFreshIdRef.current || !conversationId) return;
    awaitingFreshIdRef.current = false;
    const next = new URLSearchParams(searchParams.toString());
    next.delete("q");
    next.set("conversation", conversationId);
    router.replace(`${pathname}?${next.toString()}`);
  }, [conversationId, pathname, router, searchParams]);

  const selectConversation = useCallback(
    (id: string) => router.push(`${pathname}?conversation=${encodeURIComponent(id)}`),
    [pathname, router],
  );
  const newConversation = useCallback(() => router.push(pathname), [pathname, router]);
  const buildCitationHref = useCallback(
    (source: { pageId: string }) => pageHref(repoId, source.pageId),
    [repoId],
  );
  const sendWithConversationModel = useCallback((text: string) => sendMessage(text, {
    context: pageContext,
    ...(selectedProvider ? { provider: selectedProvider } : {}),
    ...(selectedModel ? { model: selectedModel } : {}),
  }), [pageContext, selectedModel, selectedProvider, sendMessage]);
  const retryMessage = useCallback((message: ChatUIMessage) => {
    const index = messages.findIndex((candidate) => candidate.id === message.id);
    const previousUser = messages.slice(0, index).reverse().find((candidate) => candidate.role === "user");
    if (previousUser) return sendWithConversationModel(previousUser.text);
  }, [messages, sendWithConversationModel]);
  const editAndResend = useCallback(async (message: ChatUIMessage, text: string) => {
    if (!conversationId || !message.serverId) return;
    const fork = await forkConversation(repoId, conversationId, { beforeMessageId: message.serverId });
    await loadConversation(fork.id);
    router.push(`${pathname}?conversation=${encodeURIComponent(fork.id)}`);
    await sendWithConversationModel(text);
  }, [conversationId, loadConversation, pathname, repoId, router, sendWithConversationModel]);
  const updateArtifactUrl = useCallback((key: "artifact" | "compare", artifactId: string | null) => {
    const next = new URLSearchParams(searchParamsRef.current.toString());
    if (artifactId) next.set(key, artifactId);
    else {
      next.delete(key);
      if (key === "artifact") next.delete("compare");
    }
    const query = next.toString();
    router.replace(query ? `${pathname}?${query}` : pathname);
  }, [pathname, router]);
  const pinArtifact = useCallback(async (artifact: ChatArtifact, pinned: boolean) => {
    if (!conversationId) return;
    replaceArtifact(await setConversationArtifactPinned(repoId, conversationId, artifact.id, pinned));
  }, [conversationId, repoId, replaceArtifact]);
  const navigateArtifact = useCallback((artifactId: string | null) => updateArtifactUrl("artifact", artifactId), [updateArtifactUrl]);
  const compareArtifact = useCallback((artifactId: string | null) => updateArtifactUrl("compare", artifactId), [updateArtifactUrl]);
  const openArtifactSource = useCallback((artifact: ChatArtifact) => {
    const target = getArtifactSourceTarget(artifact);
    if (target?.pageId) router.push(pageHref(repoId, target.pageId));
    else if (target?.path) router.push(pageHref(repoId, `file_page:${target.path}`));
  }, [repoId, router]);

  return (
    <div className="flex h-full min-h-0 overflow-hidden">
      <ConversationHistory
        repoId={repoId}
        activeConversationId={conversationId}
        onSelect={selectConversation}
        onNew={newConversation}
        variant="rail"
        collapsible
        railPreferenceKey={`repowise:chat-history-rail:${repoId}`}
        className="hidden h-full shrink-0 overflow-hidden transition-[width] duration-150 lg:block lg:data-[collapsed=true]:w-12 lg:data-[collapsed=false]:w-64 2xl:data-[collapsed=false]:w-72 motion-reduce:transition-none"
      />
      <div className="h-full min-h-0 min-w-0 flex-1">
      <ChatInterfaceShell
      repoId={repoId}
      {...(repoName !== undefined ? { repoName } : {})}
      context={pageContext}
      messages={messages}
      isStreaming={isStreaming}
      error={error}
      onSend={sendWithConversationModel}
      onCancel={cancel}
      draft={draft}
      onDraftChange={setDraft}
      buildCitationHref={buildCitationHref}
      modelSelectorSlot={<ModelSelector repoId={repoId} activeProvider={selectedProvider} activeModel={selectedModel} onSelect={selectModel} />}
      onRetry={retryMessage}
      onEditAndResend={editAndResend}
      activeArtifactId={activeArtifactId}
      compareArtifactId={compareArtifactId}
      onArtifactNavigate={navigateArtifact}
      onArtifactCompare={compareArtifact}
      onArtifactPin={pinArtifact}
      onOpenArtifactSource={openArtifactSource}
      artifactOverrides={artifactOverrides}
      statusSlot={
        // Orientation, in one line: what was indexed, and which commit it was
        // indexed from. The branch and SHA used to sit in a second page header
        // that duplicated the breadcrumb; they belong with the other figures.
        <span>
          {[
            stats ? `${stats.file_count.toLocaleString()} files` : null,
            stats ? `${Math.round(stats.doc_coverage_pct)}% documented` : null,
            stats && stats.symbol_count > 0
              ? `${stats.symbol_count.toLocaleString()} symbols indexed`
              : null,
            defaultBranch,
            headCommit ? headCommit.slice(0, 7) : null,
          ]
            .filter(Boolean)
            .join(" · ")}
        </span>
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
        <div className="lg:hidden">
          <ConversationHistory
            repoId={repoId}
            activeConversationId={conversationId}
            onSelect={selectConversation}
            onNew={newConversation}
          />
        </div>
      }
    />
      </div>
    </div>
  );
}
