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
  router: null as { replace: ReturnType<typeof vi.fn>; push: ReturnType<typeof vi.fn> } | null,
  conversation: null as string | null,
  artifact: null as string | null,
  compare: null as string | null,
  activeConversation: null as string | null,
  shellProps: null as Record<string, unknown> | null,
}));

vi.mock("next/navigation", () => ({
  usePathname: () => "/repos/r1/chat",
  useRouter: () => mocks.router,
  useSearchParams: () => new URLSearchParams([
    ...(mocks.conversation ? [["conversation", mocks.conversation]] : []),
    ...(mocks.artifact ? [["artifact", mocks.artifact]] : []),
    ...(mocks.compare ? [["compare", mocks.compare]] : []),
  ]),
}));
vi.mock("swr", () => ({ default: () => ({ data: undefined }) }));
vi.mock("next/link", () => ({ default: ({ children }: { children: React.ReactNode }) => <>{children}</> }));
vi.mock("@repowise-dev/ui/chat/chat-interface", () => ({ ChatInterface: (props: Record<string, unknown>) => { mocks.shellProps = props; return <div />; } }));
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
    mocks.router = { replace: mocks.replace, push: mocks.push };
    mocks.conversation = null;
    mocks.activeConversation = null;
    mocks.artifact = null;
    mocks.compare = null;
    mocks.shellProps = null;
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

  it("passes artifact deep links through and preserves the conversation when navigating artifacts", async () => {
    mocks.conversation = "c42";
    mocks.artifact = "a1";
    mocks.compare = "a2";
    render(<ChatInterface repoId="r1" />);
    await waitFor(() => expect(mocks.shellProps).not.toBeNull());
    expect(mocks.shellProps).toMatchObject({ activeArtifactId: "a1", compareArtifactId: "a2" });
    (mocks.shellProps?.onArtifactNavigate as (id: string | null) => void)("a3");
    expect(mocks.replace).toHaveBeenCalledWith("/repos/r1/chat?conversation=c42&artifact=a3&compare=a2");
    (mocks.shellProps?.onArtifactNavigate as (id: string | null) => void)(null);
    expect(mocks.replace).toHaveBeenCalledWith("/repos/r1/chat?conversation=c42");
  });

  it("keeps artifact navigation callbacks stable across URL-only updates", async () => {
    mocks.conversation = "c42";
    mocks.artifact = "a1";
    const view = render(<ChatInterface repoId="r1" />);
    await waitFor(() => expect(mocks.shellProps).not.toBeNull());
    const navigate = mocks.shellProps?.onArtifactNavigate;
    const compare = mocks.shellProps?.onArtifactCompare;
    mocks.artifact = "a2";
    mocks.compare = "a1";
    view.rerender(<ChatInterface repoId="r1" />);
    expect(mocks.shellProps?.onArtifactNavigate).toBe(navigate);
    expect(mocks.shellProps?.onArtifactCompare).toBe(compare);
  });
});
