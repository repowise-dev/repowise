// @vitest-environment jsdom

import React from "react";
import { render, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ChatInterface } from "./chat-interface";

const mocks = vi.hoisted(() => ({
  reset: vi.fn(),
  loadConversation: vi.fn(),
  sendMessage: vi.fn(),
  replace: vi.fn(),
  push: vi.fn(),
  conversation: null as string | null,
  activeConversation: null as string | null,
}));

vi.mock("next/navigation", () => ({
  usePathname: () => "/repos/r1/chat",
  useRouter: () => ({ replace: mocks.replace, push: mocks.push }),
  useSearchParams: () => new URLSearchParams(
    mocks.conversation ? `conversation=${mocks.conversation}` : "",
  ),
}));
vi.mock("swr", () => ({ default: () => ({ data: undefined }) }));
vi.mock("next/link", () => ({ default: ({ children }: { children: React.ReactNode }) => <>{children}</> }));
vi.mock("@repowise-dev/ui/chat/chat-interface", () => ({ ChatInterface: () => <div /> }));
vi.mock("./model-selector", () => ({ ModelSelector: () => <div /> }));
vi.mock("./conversation-history", () => ({ ConversationHistory: () => <div /> }));
vi.mock("./repository-chat-provider", () => ({
  useRepositoryChat: () => ({
    messages: [], conversationId: mocks.activeConversation, isStreaming: false, error: null,
    sendMessage: mocks.sendMessage, loadConversation: mocks.loadConversation,
    cancel: vi.fn(), reset: mocks.reset,
    pageContext: { kind: "chat", label: "Chat" },
    selectedProvider: null, selectedModel: null, selectModel: vi.fn(),
  }),
}));

describe("chat route conversation lifecycle", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.conversation = null;
    mocks.activeConversation = null;
  });

  it("starts a fresh conversation on the plain chat URL", async () => {
    render(<ChatInterface repoId="r1" />);
    await waitFor(() => expect(mocks.reset).toHaveBeenCalledOnce());
    expect(mocks.loadConversation).not.toHaveBeenCalled();
  });

  it("restores only the conversation named by the URL", async () => {
    mocks.conversation = "c42";
    render(<ChatInterface repoId="r1" />);
    await waitFor(() => expect(mocks.loadConversation).toHaveBeenCalledWith("c42"));
    expect(mocks.reset).not.toHaveBeenCalled();
  });

  it("does not reload a newly completed conversation when its id is written to the URL", async () => {
    const view = render(<ChatInterface repoId="r1" />);
    await waitFor(() => expect(mocks.reset).toHaveBeenCalledOnce());
    mocks.activeConversation = "c-new";
    view.rerender(<ChatInterface repoId="r1" />);
    await waitFor(() => expect(mocks.replace).toHaveBeenCalledWith("/repos/r1/chat?conversation=c-new"));
    mocks.conversation = "c-new";
    view.rerender(<ChatInterface repoId="r1" />);
    expect(mocks.loadConversation).not.toHaveBeenCalled();
  });
});
