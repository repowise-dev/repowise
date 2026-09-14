// @vitest-environment jsdom

import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { SWRConfig } from "swr";
import {
  RepositoryChatProvider,
  useRepositoryChat,
} from "./repository-chat-provider";

let pathname = "/repos/r1/files/src/a.py";
let search = new URLSearchParams();

vi.mock("next/navigation", () => ({
  usePathname: () => pathname,
  useSearchParams: () => search,
}));

vi.mock("@/lib/hooks/use-chat", () => ({
  useChat: () => ({
    messages: [],
    conversationId: null,
    isStreaming: false,
    error: null,
    sendMessage: vi.fn(),
    loadConversation: vi.fn(),
    cancel: vi.fn(),
    reset: vi.fn(),
    artifactOverrides: {},
    replaceArtifact: vi.fn(),
  }),
}));

const getChatSuggestions = vi.fn();
vi.mock("@/lib/api/chat", () => ({
  getChatSuggestions: (...args: unknown[]) => getChatSuggestions(...args),
}));

function Probe() {
  const { suggestions } = useRepositoryChat();
  return (
    <span data-testid="suggestions">
      {suggestions === undefined ? "none" : suggestions.map((s) => s.text).join("|")}
    </span>
  );
}

/** A fresh cache per test: the SWR key is shared across these cases, and a
 *  carried-over entry would answer the next one without a request. */
function shell() {
  return (
    <SWRConfig value={{ provider: () => new Map(), dedupingInterval: 0 }}>
      <RepositoryChatProvider repoId="r1" repoName="acme">
        <Probe />
      </RepositoryChatProvider>
    </SWRConfig>
  );
}

function renderShell() {
  return render(shell());
}

function shown(): string {
  return screen.getByTestId("suggestions").textContent ?? "";
}

describe("the page tier the provider fetches", () => {
  beforeEach(() => {
    pathname = "/repos/r1/files/src/a.py";
    search = new URLSearchParams();
    getChatSuggestions.mockReset();
    window.localStorage.clear();
  });
  afterEach(cleanup);

  it("asks for the page the reader is on", async () => {
    getChatSuggestions.mockResolvedValue({
      suggestions: [{ text: "Explain the 28 bug fixes in a.py", source: "page" }],
    });
    renderShell();

    await waitFor(() => expect(shown()).toBe("Explain the 28 bug fixes in a.py"));
    expect(getChatSuggestions).toHaveBeenCalledWith("r1", {
      kind: "file",
      target: "src/a.py",
    });
  });

  it("does not ask when the route names no target", async () => {
    pathname = "/repos/r1/overview";
    renderShell();

    await waitFor(() => expect(shown()).toBe("none"));
    expect(getChatSuggestions).not.toHaveBeenCalled();
  });

  it("leaves the static tier standing when the page measured nothing", async () => {
    getChatSuggestions.mockResolvedValue({ suggestions: [] });
    renderShell();

    await waitFor(() => expect(getChatSuggestions).toHaveBeenCalled());
    expect(shown()).toBe("none");
  });

  it("keeps the composer usable when the request fails", async () => {
    getChatSuggestions.mockRejectedValue(new Error("offline"));
    renderShell();

    await waitFor(() => expect(getChatSuggestions).toHaveBeenCalled());
    expect(shown()).toBe("none");
  });

  it("asks again for a different file rather than holding the last one", async () => {
    getChatSuggestions.mockResolvedValue({
      suggestions: [{ text: "About a.py", source: "page" }],
    });
    const { rerender } = renderShell();
    await waitFor(() => expect(shown()).toBe("About a.py"));

    pathname = "/repos/r1/files/src/b.py";
    getChatSuggestions.mockResolvedValue({
      suggestions: [{ text: "About b.py", source: "page" }],
    });
    rerender(shell());

    await waitFor(() => expect(shown()).toBe("About b.py"));
    expect(getChatSuggestions).toHaveBeenLastCalledWith("r1", {
      kind: "file",
      target: "src/b.py",
    });
  });
});
