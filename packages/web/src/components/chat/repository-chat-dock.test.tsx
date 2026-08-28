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
  }),
}));

describe("RepositoryChatDock", () => {
  it("clears the full graph control stack", () => {
    expect(REPOSITORY_GRAPH_DOCK_INSET).toBe("12.5rem");
    expect(getRepositoryDockCollisionInset("architecture")).toBe("12.5rem");
    expect(getRepositoryDockCollisionInset("graph")).toBe("12.5rem");
    expect(getRepositoryDockCollisionInset("health")).toBeUndefined();
  });

  it("does not mount duplicate chat UI on the full chat route", () => {
    const { container } = render(<RepositoryChatDock />);
    expect(container.innerHTML).toBe("");
    expect(screen.queryByLabelText("Repository chat")).toBeNull();
  });
});
