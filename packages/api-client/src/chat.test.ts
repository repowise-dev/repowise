import { describe, expect, it, vi, beforeEach } from "vitest";

vi.mock("./client", () => ({
  apiGet: vi.fn(),
  apiDelete: vi.fn(),
  BASE_URL: "http://localhost:7337",
  buildHeaders: () => new Headers(),
}));

import { postChatMessage } from "./chat";

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
