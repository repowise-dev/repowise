"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { postChatMessage, getConversation } from "@/lib/api/chat";
import type { ChatSSEEvent } from "@/lib/api/types";
import type {
  ChatContext,
  ChatUIToolCall as ChatToolCall,
  ChatUIMessage as ChatMessage,
} from "@repowise-dev/types/chat";
import { toFriendlyMessage } from "@repowise-dev/ui/lib/errors";
import { toChatUiMessages } from "@/lib/chat/to-chat-ui-messages";

export type { ChatToolCall, ChatMessage };

export interface UseChatState {
  messages: ChatMessage[];
  conversationId: string | null;
  isStreaming: boolean;
  error: string | null;
}

const EMPTY_CHAT_STATE: UseChatState = {
  messages: [],
  conversationId: null,
  isStreaming: false,
  error: null,
};

// ---------------------------------------------------------------------------
// Hook
// ---------------------------------------------------------------------------

export function useChat(repoId: string) {
  const [state, setState] = useState<UseChatState>(EMPTY_CHAT_STATE);

  const abortRef = useRef<AbortController | null>(null);
  const activeRepoRef = useRef(repoId);

  useEffect(() => {
    if (activeRepoRef.current !== repoId) {
      abortRef.current?.abort();
      abortRef.current = null;
      activeRepoRef.current = repoId;
      setState(EMPTY_CHAT_STATE);
    }
    return () => abortRef.current?.abort();
  }, [repoId]);

  const sendMessage = useCallback(
    async (
      text: string,
      opts?: { provider?: string; model?: string; context?: ChatContext },
    ) => {
      abortRef.current?.abort();
      const abort = new AbortController();
      abortRef.current = abort;

      const userMsgId = `user-${Date.now()}`;
      const asstMsgId = `asst-${Date.now()}`;

      setState((prev) => ({
        ...prev,
        isStreaming: true,
        error: null,
        messages: [
          ...prev.messages,
          {
            id: userMsgId,
            role: "user",
            text,
            toolCalls: [],
            isStreaming: false,
          },
          {
            id: asstMsgId,
            role: "assistant",
            text: "",
            toolCalls: [],
            isStreaming: true,
          },
        ],
      }));

      // The server closes the stream with a `done` or an `error` event, and
      // those are the only things that clear `isStreaming`. A stream that ends
      // without either one (an early return in the SSE generator, a dropped
      // connection, a provider that stops mid-answer) used to leave the
      // composer spinning forever with nothing on screen to explain it.
      let settled = false;

      try {
        const res = await postChatMessage(repoId, {
          message: text,
          conversationId:
            activeRepoRef.current === repoId
              ? state.conversationId ?? undefined
              : undefined,
          provider: opts?.provider,
          model: opts?.model,
          context: opts?.context,
          signal: abort.signal,
        });

        if (!res.body) throw new Error("No response body");

        const reader = res.body.getReader();
        const decoder = new TextDecoder();
        let buffer = "";

        while (true) {
          const { done, value } = await reader.read();
          if (done || abort.signal.aborted) break;

          buffer += decoder.decode(value, { stream: true });

          // Parse SSE lines
          const lines = buffer.split("\n");
          buffer = lines.pop() ?? "";

          for (const line of lines) {
            if (line.startsWith("data: ")) {
              const data = line.slice(6);
              try {
                const parsed = JSON.parse(data) as ChatSSEEvent;
                handleEvent(parsed, asstMsgId);
              } catch {
                // malformed line
              }
            }
          }
        }
      } catch (err: unknown) {
        if (!abort.signal.aborted) {
          settled = true;
          setState((prev) => ({
            ...prev,
            isStreaming: false,
            error: toFriendlyMessage(err),
            messages: prev.messages.map((m) =>
              m.id === asstMsgId ? { ...m, isStreaming: false } : m,
            ),
          }));
        }
      }

      if (!settled && !abort.signal.aborted) {
        setState((prev) => ({
          ...prev,
          isStreaming: false,
          error:
            prev.error ??
            "The response ended before it finished. The server log should say why.",
          messages: prev.messages.map((m) =>
            m.id === asstMsgId ? { ...m, isStreaming: false } : m,
          ),
        }));
      }

      function handleEvent(ev: ChatSSEEvent, asstId: string) {
        if (activeRepoRef.current !== repoId) return;
        if (ev.type === "done" || ev.type === "error") settled = true;
        setState((prev) => {
          const messages = prev.messages.map((m) => {
            if (m.id !== asstId) return m;

            switch (ev.type) {
              case "text_delta":
                return { ...m, text: m.text + ev.text };

              case "tool_start":
                return {
                  ...m,
                  toolCalls: [
                    ...m.toolCalls,
                    {
                      id: ev.tool_id,
                      name: ev.tool_name,
                      arguments: ev.input,
                      status: "running" as const,
                    },
                  ],
                };

              case "tool_result":
                return {
                  ...m,
                  toolCalls: m.toolCalls.map((tc) =>
                    tc.id === ev.tool_id
                      ? {
                          ...tc,
                          result: ev.artifact.data,
                          summary: ev.summary,
                          artifact: ev.artifact,
                          status: "done" as const,
                        }
                      : tc,
                  ),
                };

              case "done":
                return { ...m, isStreaming: false, serverId: ev.message_id };

              case "error":
                return { ...m, isStreaming: false };

              default:
                return m;
            }
          });

          return {
            ...prev,
            isStreaming:
              ev.type !== "done" && ev.type !== "error"
                ? prev.isStreaming
                : false,
            conversationId:
              ev.type === "done" ? ev.conversation_id : prev.conversationId,
            error: ev.type === "error" ? ev.message : prev.error,
            messages,
          };
        });
      }
    },
    [repoId, state.conversationId],
  );

  const loadConversation = useCallback(
    async (conversationId: string) => {
      try {
        const data = await getConversation(repoId, conversationId);
        if (activeRepoRef.current !== repoId) return;
        const msgs = toChatUiMessages(data.messages);
        setState({
          messages: msgs,
          conversationId,
          isStreaming: false,
          error: null,
        });
      } catch (err) {
        if (activeRepoRef.current !== repoId) return;
        setState((prev) => ({
          ...prev,
          error: toFriendlyMessage(err),
        }));
      }
    },
    [repoId],
  );

  const reset = useCallback(() => {
    abortRef.current?.abort();
    setState(EMPTY_CHAT_STATE);
  }, []);

  const visibleState = activeRepoRef.current === repoId ? state : EMPTY_CHAT_STATE;
  return { ...visibleState, sendMessage, loadConversation, reset };
}
