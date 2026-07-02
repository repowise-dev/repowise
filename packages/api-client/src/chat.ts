/**
 * Chat API module — conversation management and SSE message streaming.
 */

import { apiGet, apiDelete, BASE_URL, buildHeaders } from "./client";
import type { ConversationResponse, ChatMessageResponse } from "./types";

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
    }),
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
