"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { postChatMessage, getConversation } from "@/lib/api/chat";
import type { ChatSSEEvent } from "@/lib/api/types";
import type {
  ChatArtifact,
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

function stopRunningTools(message: ChatMessage, summary: string): ChatMessage {
  return {
    ...message,
    isStreaming: false,
    toolCalls: message.toolCalls.map((tool) =>
      tool.status === "running" ? { ...tool, status: "error" as const, summary } : tool,
    ),
  };
}

// ---------------------------------------------------------------------------
// Hook
// ---------------------------------------------------------------------------

export function useChat(repoId: string) {
  const [state, setState] = useState<UseChatState>(EMPTY_CHAT_STATE);
  const [artifactOverrides, setArtifactOverrides] = useState<Record<string, ChatArtifact>>({});

  const abortRef = useRef<AbortController | null>(null);
  const activeRepoRef = useRef(repoId);
  const conversationIdRef = useRef<string | null>(null);
  const loadRequestRef = useRef(0);

  useEffect(() => {
    if (activeRepoRef.current !== repoId) {
      abortRef.current?.abort();
      abortRef.current = null;
      loadRequestRef.current += 1;
      activeRepoRef.current = repoId;
      conversationIdRef.current = null;
      setState(EMPTY_CHAT_STATE);
      setArtifactOverrides({});
    }
    return () => abortRef.current?.abort();
  }, [repoId]);

  const sendMessage = useCallback(
    async (
      text: string,
      opts?: { provider?: string; model?: string; context?: ChatContext },
    ) => {
      abortRef.current?.abort();
      loadRequestRef.current += 1;
      const abort = new AbortController();
      abortRef.current = abort;

      const userMsgId = `user-${Date.now()}`;
      const asstMsgId = `asst-${Date.now()}`;
      let pendingText = "";
      let textFrame: number | null = null;

      const flushText = () => {
        if (textFrame !== null) {
          window.cancelAnimationFrame(textFrame);
          textFrame = null;
        }
        if (!pendingText || abort.signal.aborted || activeRepoRef.current !== repoId) {
          pendingText = "";
          return;
        }
        const text = pendingText;
        pendingText = "";
        setState((prev) => ({
          ...prev,
          messages: prev.messages.map((message) =>
            message.id === asstMsgId
              ? { ...message, text: message.text + text }
              : message,
          ),
        }));
      };

      const queueText = (text: string) => {
        pendingText += text;
        if (textFrame !== null) return;
        textFrame = window.requestAnimationFrame(() => {
          textFrame = null;
          flushText();
        });
      };

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
              ? conversationIdRef.current ?? undefined
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
        if (textFrame !== null) window.cancelAnimationFrame(textFrame);
        if (!abort.signal.aborted) {
          settled = true;
          setState((prev) => ({
            ...prev,
            isStreaming: false,
            error: toFriendlyMessage(err),
            messages: prev.messages.map((m) =>
              m.id === asstMsgId ? stopRunningTools(m, "Stopped after an error") : m,
            ),
          }));
        }
      }

      if (!settled && !abort.signal.aborted) {
        flushText();
        setState((prev) => ({
          ...prev,
          isStreaming: false,
          error:
            prev.error ??
            "The response ended before it finished. The server log should say why.",
          messages: prev.messages.map((m) =>
            m.id === asstMsgId ? stopRunningTools(m, "Response ended early") : m,
          ),
        }));
      }

      function handleEvent(ev: ChatSSEEvent, asstId: string) {
        if (activeRepoRef.current !== repoId) return;
        if (ev.type === "done" || ev.type === "error") settled = true;
        if (ev.type === "text_delta") {
          queueText(ev.text);
          return;
        }
        flushText();
        setState((prev) => {
          const messages = prev.messages.map((m) => {
            if (ev.type === "done" && m.id === userMsgId) {
              return ev.user_message_id ? { ...m, serverId: ev.user_message_id } : m;
            }
            if (m.id !== asstId) return m;

            switch (ev.type) {
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
                          result: ev.artifact.data as unknown as Record<string, unknown>,
                          summary: ev.summary,
                          artifact: ev.artifact,
                          status: "done" as const,
                        }
                      : tc,
                  ),
                };

              case "done":
                return {
                  ...m,
                  isStreaming: false,
                  serverId: ev.message_id,
                  ...(ev.provider ? { provider: ev.provider } : {}),
                  ...(ev.model ? { model: ev.model } : {}),
                };

              case "error":
                return stopRunningTools(m, ev.message);

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
        if (ev.type === "done") conversationIdRef.current = ev.conversation_id;
      }
    },
    [repoId],
  );

  const loadConversation = useCallback(
    async (conversationId: string) => {
      const requestId = ++loadRequestRef.current;
      try {
        const data = await getConversation(repoId, conversationId);
        if (activeRepoRef.current !== repoId || loadRequestRef.current !== requestId) return;
        const msgs = toChatUiMessages(data.messages);
        conversationIdRef.current = conversationId;
        setState({
          messages: msgs,
          conversationId,
          isStreaming: false,
          error: null,
        });
        setArtifactOverrides({});
      } catch (err) {
        if (activeRepoRef.current !== repoId || loadRequestRef.current !== requestId) return;
        setState((prev) => ({
          ...prev,
          error: toFriendlyMessage(err),
        }));
      }
    },
    [repoId],
  );

  const cancel = useCallback(() => {
    abortRef.current?.abort();
    abortRef.current = null;
    setState((prev) => ({
      ...prev,
      isStreaming: false,
      messages: prev.messages.map((message) =>
        message.isStreaming ? stopRunningTools(message, "Stopped") : message,
      ),
    }));
  }, []);

  const reset = useCallback(() => {
    abortRef.current?.abort();
    abortRef.current = null;
    loadRequestRef.current += 1;
    conversationIdRef.current = null;
    setState(EMPTY_CHAT_STATE);
    setArtifactOverrides({});
  }, []);

  const replaceArtifact = useCallback((artifact: ChatArtifact) => {
    setArtifactOverrides((current) => ({ ...current, [artifact.id]: artifact }));
  }, []);

  const visibleState = activeRepoRef.current === repoId ? state : EMPTY_CHAT_STATE;
  return { ...visibleState, artifactOverrides, sendMessage, loadConversation, cancel, reset, replaceArtifact };
}
