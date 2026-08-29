/**
 * Chat API module — conversation management and SSE message streaming.
 */

import { apiGet, apiDelete, apiPatch, apiPost, BASE_URL, buildHeaders } from "./client";
import type { ConversationResponse, ChatMessageResponse } from "./types";
import type { ChatArtifact, ChatContext } from "@repowise-dev/types/chat";

export async function listConversations(
  repoId: string,
): Promise<ConversationResponse[]> {
  return apiGet<ConversationResponse[]>(
    `/api/repos/${repoId}/chat/conversations`,
  );
}

export async function getConversation(
  repoId: string,
  conversationId: string,
): Promise<{
  conversation: ConversationResponse;
  messages: ChatMessageResponse[];
}> {
  return apiGet(`/api/repos/${repoId}/chat/conversations/${conversationId}`);
}

export async function deleteConversation(
  repoId: string,
  conversationId: string,
): Promise<void> {
  await apiDelete(`/api/repos/${repoId}/chat/conversations/${conversationId}`);
}

export async function restoreConversation(repoId: string, conversationId: string) {
  return apiPost<ConversationResponse>(`/api/repos/${repoId}/chat/conversations/${conversationId}/restore`);
}

export async function updateConversation(
  repoId: string,
  conversationId: string,
  patch: { title?: string; pinned?: boolean },
) {
  return apiPatch<ConversationResponse>(`/api/repos/${repoId}/chat/conversations/${conversationId}`, patch);
}

export async function forkConversation(
  repoId: string,
  conversationId: string,
  point?: { throughMessageId?: string; beforeMessageId?: string },
) {
  return apiPost<ConversationResponse>(`/api/repos/${repoId}/chat/conversations/${conversationId}/fork`, {
    through_message_id: point?.throughMessageId ?? null,
    before_message_id: point?.beforeMessageId ?? null,
  });
}

export async function getConversationArtifact(
  repoId: string,
  conversationId: string,
  artifactId: string,
): Promise<ChatArtifact> {
  return apiGet(
    `/api/repos/${repoId}/chat/conversations/${conversationId}/artifacts/${artifactId}`,
  );
}

export async function setConversationArtifactPinned(
  repoId: string,
  conversationId: string,
  artifactId: string,
  pinned: boolean,
): Promise<ChatArtifact> {
  return apiPatch(
    `/api/repos/${repoId}/chat/conversations/${conversationId}/artifacts/${artifactId}`,
    { pinned },
  );
}

/**
 * POST a chat message and return the raw Response for SSE streaming.
 * The caller reads response.body as a ReadableStream.
 */
export async function postChatMessage(
  repoId: string,
  opts: {
    message: string;
    conversationId?: string;
    provider?: string;
    model?: string;
    context?: ChatContext;
    /**
     * Aborts the request itself, not just the caller's read loop. Without it,
     * a cancelled or superseded send leaves the POST running on the server and
     * the response body abandoned, so the agentic loop keeps going and its
     * open DB session is only torn down when the socket eventually collapses.
     */
    signal?: AbortSignal;
  },
): Promise<Response> {
  const url = `${BASE_URL}/api/repos/${repoId}/chat/messages`;
  const headers = buildHeaders();
  headers.set("Accept", "text/event-stream");

  const res = await fetch(url, {
    method: "POST",
    headers,
    body: JSON.stringify({
      message: opts.message,
      conversation_id: opts.conversationId ?? null,
      provider: opts.provider ?? null,
      model: opts.model ?? null,
      context: opts.context
        ? {
            kind: opts.context.kind,
            label: opts.context.label,
            target: opts.context.target ?? null,
            target_kind: opts.context.targetKind ?? null,
          }
        : null,
    }),
    ...(opts.signal ? { signal: opts.signal } : {}),
  });

  if (!res.ok) {
    let detail = res.statusText;
    try {
      const json = (await res.json()) as { detail?: string };
      detail = json.detail ?? detail;
    } catch {
      // not JSON
    }
    throw new Error(`Chat error ${res.status}: ${detail}`);
  }

  return res;
}
