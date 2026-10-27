import { describe, expect, it, vi, beforeEach } from "vitest";

vi.mock("./client", () => ({
  apiGet: vi.fn(),
  apiDelete: vi.fn(),
  apiPatch: vi.fn(),
  apiPost: vi.fn(),
  BASE_URL: "http://localhost:7337",
  buildHeaders: () => new Headers(),
}));

import { apiGet, apiPatch } from "./client";
import { getConversationArtifact, postChatMessage, setConversationArtifactPinned } from "./chat";

function okStream(): Response {
  return new Response("retry: 3000\n\n", {
    status: 200,
    headers: { "Content-Type": "text/event-stream" },
  });
}

describe("postChatMessage", () => {
  const fetchMock = vi.fn();

  beforeEach(() => {
    fetchMock.mockReset().mockResolvedValue(okStream());
    vi.stubGlobal("fetch", fetchMock);
  });

  it("passes the caller's abort signal to fetch", async () => {
    const controller = new AbortController();
    await postChatMessage("r1", { message: "hi", signal: controller.signal });

    // Without this the request outlives a cancelled send: the server keeps
    // running the agentic loop against a body nobody reads.
    const init = fetchMock.mock.calls[0]![1] as RequestInit;
    expect(init.signal).toBe(controller.signal);
  });

  it("omits signal entirely when the caller gives none", async () => {
    await postChatMessage("r1", { message: "hi" });

    const init = fetchMock.mock.calls[0]![1] as RequestInit;
    expect("signal" in init).toBe(false);
  });

  it("serializes portable navigation context for the agent", async () => {
    await postChatMessage("r1", {
      message: "What does this do?",
      context: {
        kind: "symbol",
        label: "Symbols",
        target: "useChat",
        targetKind: "symbol",
      },
    });

    const init = fetchMock.mock.calls[0]![1] as RequestInit;
    expect(JSON.parse(String(init.body))).toMatchObject({
      context: {
        kind: "symbol",
        label: "Symbols",
        target: "useChat",
        target_kind: "symbol",
      },
    });
  });
});

describe("conversation artifacts", () => {
  it("uses repository-scoped deep-link and pin endpoints", async () => {
    vi.mocked(apiGet).mockResolvedValueOnce({ id: "a1" });
    vi.mocked(apiPatch).mockResolvedValueOnce({ id: "a1", pinned: true });
    await getConversationArtifact("r1", "c1", "a1");
    await setConversationArtifactPinned("r1", "c1", "a1", true);
    expect(apiGet).toHaveBeenCalledWith("/api/repos/r1/chat/conversations/c1/artifacts/a1");
    expect(apiPatch).toHaveBeenCalledWith("/api/repos/r1/chat/conversations/c1/artifacts/a1", { pinned: true });
  });
});
