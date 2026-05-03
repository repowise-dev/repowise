"use client";

import { useCallback, useRef, useState } from "react";
import { postChatMessage, getConversation } from "@/lib/api/chat";
import type { ChatSSEEvent } from "@/lib/api/types";
import type {
  ChatUIToolCall as ChatToolCall,
  ChatUIMessage as ChatMessage,
} from "@repowise-dev/types/chat";

export type { ChatToolCall, ChatMessage };

export interface UseChatState {
  messages: ChatMessage[];
  conversationId: string | null;
  isStreaming: boolean;
  error: string | null;
}

// ---------------------------------------------------------------------------
// Hook
// ---------------------------------------------------------------------------

export function useChat(repoId: string) {
  const [state, setState] = useState<UseChatState>({
    messages: [],
    conversationId: null,
    isStreaming: false,
    error: null,
  });

  const abortRef = useRef<AbortController | null>(null);

  const sendMessage = useCallback(
    async (text: string, opts?: { provider?: string; model?: string }) => {
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

      try {
        const res = await postChatMessage(repoId, {
          message: text,
          conversationId: state.conversationId ?? undefined,
          provider: opts?.provider,
          model: opts?.model,
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
          setState((prev) => ({
            ...prev,
            isStreaming: false,
            error: err instanceof Error ? err.message : String(err),
            messages: prev.messages.map((m) =>
              m.id === asstMsgId ? { ...m, isStreaming: false } : m,
            ),
          }));
        }
      }

      function handleEvent(ev: ChatSSEEvent, asstId: string) {
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
        const msgs: ChatMessage[] = data.messages.map((m) => ({
          id: m.id,
          serverId: m.id,
          role: m.role,
          text: m.content.text ?? "",
          toolCalls: (m.content.tool_calls ?? []).map((tc) => ({
            id: tc.id,
            name: tc.name,
            arguments: tc.arguments ?? {},
            result: tc.result,
            status: "done" as const,
          })),
          isStreaming: false,
        }));
        setState({
          messages: msgs,
          conversationId,
          isStreaming: false,
          error: null,
        });
      } catch (err) {
        setState((prev) => ({
          ...prev,
          error: err instanceof Error ? err.message : String(err),
        }));
      }
    },
    [repoId],
  );

  const reset = useCallback(() => {
    abortRef.current?.abort();
    setState({
      messages: [],
      conversationId: null,
      isStreaming: false,
      error: null,
    });
  }, []);

  return { ...state, sendMessage, loadConversation, reset };
}
