// @vitest-environment jsdom

import { act, renderHook } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { useChat } from "./use-chat";

const mocks = vi.hoisted(() => ({
  signal: undefined as AbortSignal | undefined,
  getConversation: vi.fn(),
  postChatMessage: vi.fn(),
}));

vi.mock("@/lib/api/chat", () => ({
  getConversation: mocks.getConversation,
  postChatMessage: mocks.postChatMessage,
}));

describe("useChat lifecycle", () => {
  beforeEach(() => {
    mocks.signal = undefined;
    mocks.getConversation.mockReset();
    mocks.postChatMessage.mockReset();
    mocks.postChatMessage.mockImplementation(
      (_repoId: string, options: { signal?: AbortSignal }) => {
        mocks.signal = options.signal;
        return new Promise<Response>(() => undefined);
      },
    );
  });

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

  it("cancels streaming without erasing the conversation", () => {
    const { result } = renderHook(() => useChat("r1"));

    act(() => {
      void result.current.sendMessage("Explain this page");
    });
    expect(result.current.messages).toHaveLength(2);
    expect(result.current.isStreaming).toBe(true);

    act(() => result.current.cancel());

    expect(mocks.signal?.aborted).toBe(true);
    expect(result.current.messages).toHaveLength(2);
    expect(result.current.messages.at(-1)?.isStreaming).toBe(false);
    expect(result.current.isStreaming).toBe(false);
  });

  it("buffers rapid text deltas until the next animation frame", async () => {
    let frame: FrameRequestCallback | undefined;
    vi.stubGlobal(
      "requestAnimationFrame",
      vi.fn((callback: FrameRequestCallback) => {
        frame = callback;
        return 1;
      }),
    );
    vi.stubGlobal("cancelAnimationFrame", vi.fn());
    let streamController!: ReadableStreamDefaultController<Uint8Array>;
    const stream = new ReadableStream<Uint8Array>({
      start(controller) {
        streamController = controller;
      },
    });
    mocks.postChatMessage.mockImplementationOnce(
      (_repoId: string, options: { signal?: AbortSignal }) => {
        mocks.signal = options.signal;
        return Promise.resolve(new Response(stream));
      },
    );
    const { result } = renderHook(() => useChat("r1"));

    act(() => {
      void result.current.sendMessage("Explain this page");
    });
    await act(async () => {
      streamController.enqueue(
        new TextEncoder().encode(
          ["a", "b", "c"]
            .map((text) => `data: ${JSON.stringify({ type: "text_delta", text })}\n`)
            .join(""),
        ),
      );
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(result.current.messages.at(-1)?.text).toBe("");
    expect(requestAnimationFrame).toHaveBeenCalledTimes(1);
    act(() => frame?.(16));
    expect(result.current.messages.at(-1)?.text).toBe("abc");

    act(() => result.current.cancel());
    vi.unstubAllGlobals();
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

  it("lets only the newest conversation load update state", async () => {
    let resolveFirst!: (value: unknown) => void;
    let resolveSecond!: (value: unknown) => void;
    mocks.getConversation
      .mockReturnValueOnce(new Promise((resolve) => { resolveFirst = resolve; }))
      .mockReturnValueOnce(new Promise((resolve) => { resolveSecond = resolve; }));
    const { result } = renderHook(() => useChat("r1"));
    let first!: Promise<void>;
    let second!: Promise<void>;
    act(() => {
      first = result.current.loadConversation("c1");
      second = result.current.loadConversation("c2");
    });
    const response = (id: string, text: string) => ({ messages: [{ id: `m-${id}`, conversation_id: id, role: "assistant", content: { text }, created_at: "2026-08-28T00:00:00Z" }] });
    await act(async () => { resolveSecond(response("c2", "newest")); await second; });
    await act(async () => { resolveFirst(response("c1", "stale")); await first; });
    expect(result.current.conversationId).toBe("c2");
    expect(result.current.messages[0]?.text).toBe("newest");
  });

  it("does not restore a load that resolves after reset", async () => {
    let resolveLoad!: (value: unknown) => void;
    mocks.getConversation.mockReturnValueOnce(new Promise((resolve) => { resolveLoad = resolve; }));
    const { result } = renderHook(() => useChat("r1"));
    let loading!: Promise<void>;
    act(() => { loading = result.current.loadConversation("c1"); });
    act(() => result.current.reset());
    await act(async () => {
      resolveLoad({ messages: [{ id: "m1", conversation_id: "c1", role: "assistant", content: { text: "stale" }, created_at: "2026-08-28T00:00:00Z" }] });
      await loading;
    });
    expect(result.current.messages).toEqual([]);
    expect(result.current.conversationId).toBeNull();
  });

  it("stores artifact mutations outside transcript messages", () => {
    const { result } = renderHook(() => useChat("r1"));
    const artifact = { id: "a1", version: 1 as const, type: "risk", tool_name: "get_risk", presentation: "risk", pinned: true, data: {} };
    const messages = result.current.messages;
    act(() => result.current.replaceArtifact(artifact));
    expect(result.current.artifactOverrides.a1).toEqual(artifact);
    expect(result.current.messages).toBe(messages);
  });

});
