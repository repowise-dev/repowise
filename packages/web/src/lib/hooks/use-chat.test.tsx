// @vitest-environment jsdom

import { act, renderHook } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { useChat } from "./use-chat";

const mocks = vi.hoisted(() => ({
  signal: undefined as AbortSignal | undefined,
  getConversation: vi.fn(),
}));

vi.mock("@/lib/api/chat", () => ({
  getConversation: mocks.getConversation,
  postChatMessage: vi.fn(
    (_repoId: string, options: { signal?: AbortSignal }) => {
      mocks.signal = options.signal;
      return new Promise<Response>(() => undefined);
    },
  ),
}));

describe("useChat lifecycle", () => {
  it("aborts an in-flight request when its repository owner unmounts", () => {
    const { result, unmount } = renderHook(() => useChat("r1"));

    act(() => {
      void result.current.sendMessage("Explain this page");
    });
    expect(mocks.signal?.aborted).toBe(false);

    unmount();
    expect(mocks.signal?.aborted).toBe(true);
  });

  it("clears repository-scoped state when the repository changes", () => {
    const { result, rerender } = renderHook(
      ({ repoId }) => useChat(repoId),
      { initialProps: { repoId: "r1" } },
    );

    act(() => {
      void result.current.sendMessage("Explain repository one");
    });
    expect(result.current.messages).toHaveLength(2);
    const firstSignal = mocks.signal;

    rerender({ repoId: "r2" });

    expect(firstSignal?.aborted).toBe(true);
    expect(result.current.messages).toEqual([]);
    expect(result.current.conversationId).toBeNull();
  });

  it.each(["resolve", "reject"] as const)(
    "ignores a late conversation %s from the previous repository",
    async (outcome) => {
      let resolveLoad!: (value: unknown) => void;
      let rejectLoad!: (reason: unknown) => void;
      mocks.getConversation.mockReturnValueOnce(
        new Promise((resolve, reject) => {
          resolveLoad = resolve;
          rejectLoad = reject;
        }),
      );
      const { result, rerender } = renderHook(
        ({ repoId }) => useChat(repoId),
        { initialProps: { repoId: "r1" } },
      );

      let loading!: Promise<void>;
      act(() => {
        loading = result.current.loadConversation("c1");
      });
      rerender({ repoId: "r2" });
      await act(async () => {
        if (outcome === "resolve") {
          resolveLoad({
            messages: [
              {
                id: "m1",
                conversation_id: "c1",
                role: "assistant",
                content: { text: "Repository one" },
                created_at: "2026-08-28T00:00:00Z",
              },
            ],
          });
        } else {
          rejectLoad(new Error("Repository one failed"));
        }
        await loading;
      });

      expect(result.current.messages).toEqual([]);
      expect(result.current.error).toBeNull();
    },
  );
});
