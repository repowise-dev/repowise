// @vitest-environment jsdom

import React from "react";
import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { hidesRepositoryDockPill, RepositoryChatDock } from "./repository-chat-dock";

vi.mock("./repository-chat-provider", () => ({
  useRepositoryChat: () => ({
    repoId: "r1",
    repoName: "acme",
    pageContext: { kind: "chat", label: "Chat" },
    messages: [],
    conversationId: null,
    isStreaming: false,
    error: null,
    sendMessage: vi.fn(),
    loadConversation: vi.fn(),
    reset: vi.fn(),
    selectedProvider: null,
    selectedModel: null,
    selectModel: vi.fn(),
  }),
}));

describe("RepositoryChatDock", () => {
  it("hides the pill on the Architecture Map canvas", () => {
    // No `?view=` is the Map tab's landing state, and both graph scopes are it.
    expect(hidesRepositoryDockPill("architecture")).toBe(true);
    expect(hidesRepositoryDockPill("architecture", "communities")).toBe(true);
    expect(hidesRepositoryDockPill("architecture", "files")).toBe(true);
  });

  it("keeps the pill everywhere else", () => {
    // Architecture's table tabs and the tree outline mount no canvas, and
    // other pages, the Knowledge Graph included, own no bottom-right controls.
    for (const view of ["coupling", "packages", "symbols", "deps", "tree"]) {
      expect(hidesRepositoryDockPill("architecture", view)).toBe(false);
    }
    expect(hidesRepositoryDockPill("graph")).toBe(false);
    expect(hidesRepositoryDockPill("health")).toBe(false);
  });

  it("does not mount duplicate chat UI on the full chat route", () => {
    const { container } = render(<RepositoryChatDock />);
    expect(container.innerHTML).toBe("");
    expect(screen.queryByLabelText("Repository chat")).toBeNull();
  });
});
