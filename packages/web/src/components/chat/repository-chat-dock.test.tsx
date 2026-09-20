// @vitest-environment jsdom

import React from "react";
import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import {
  getRepositoryDockCollisionInset,
  REPOSITORY_GRAPH_DOCK_INSET,
  RepositoryChatDock,
} from "./repository-chat-dock";

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
  it("clears the full graph control stack on the Map tab", () => {
    expect(REPOSITORY_GRAPH_DOCK_INSET).toBe("12.5rem");
    // No `?view=` is the Map tab's landing state, and both graph scopes are it.
    expect(getRepositoryDockCollisionInset("architecture")).toBe("12.5rem");
    expect(getRepositoryDockCollisionInset("architecture", "communities")).toBe("12.5rem");
    expect(getRepositoryDockCollisionInset("architecture", "files")).toBe("12.5rem");
    expect(getRepositoryDockCollisionInset("health")).toBeUndefined();
  });

  it("does not lift the dock where nothing is under it", () => {
    // The Knowledge Graph anchors its only control bottom-LEFT, and these
    // Architecture tabs render tables with no canvas. Lifting the pill 200px
    // on any of them is dead space.
    expect(getRepositoryDockCollisionInset("graph")).toBeUndefined();
    expect(getRepositoryDockCollisionInset("architecture", "coupling")).toBeUndefined();
    expect(getRepositoryDockCollisionInset("architecture", "packages")).toBeUndefined();
    expect(getRepositoryDockCollisionInset("architecture", "symbols")).toBeUndefined();
    expect(getRepositoryDockCollisionInset("architecture", "deps")).toBeUndefined();
  });

  it("does not mount duplicate chat UI on the full chat route", () => {
    const { container } = render(<RepositoryChatDock />);
    expect(container.innerHTML).toBe("");
    expect(screen.queryByLabelText("Repository chat")).toBeNull();
  });
});
