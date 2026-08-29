import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { ChatInterface } from "../../src/chat/chat-interface.js";
import type { ChatUIMessage } from "@repowise-dev/types/chat";

const ASSISTANT_MSG: ChatUIMessage = {
  id: "asst-1",
  role: "assistant",
  text: "Hello — here is an overview.",
  toolCalls: [],
  isStreaming: false,
};

const USER_MSG: ChatUIMessage = {
  id: "user-1",
  role: "user",
  text: "Give me an overview",
  toolCalls: [],
  isStreaming: false,
};

describe("ChatInterface shell", () => {
  it("restores and opens a durable artifact by stable ID", () => {
    const withArtifact: ChatUIMessage = {
      ...ASSISTANT_MSG,
      toolCalls: [{
        id: "tool-1",
        name: "search_codebase",
        arguments: {},
        status: "done",
        artifact: {
          id: "artifact-1",
          version: 1,
          type: "search_results",
          tool_name: "search_codebase",
          title: "Chat search",
          presentation: "search_results",
          evidence: { basis: "unknown" },
          data: { query: "chat", results: [] },
        },
      }],
    };
    render(<ChatInterface repoId="r1" messages={[withArtifact]} activeArtifactId="artifact-1" isStreaming={false} onSend={vi.fn()} onCancel={vi.fn()} />);
    expect(screen.getByText("Artifact workspace")).toBeInTheDocument();
    expect(screen.getByText("Chat search")).toBeInTheDocument();
    expect(screen.getByText("No results found.")).toBeInTheDocument();
  });

  it("clears comparison when its artifact becomes the primary selection", () => {
    const onArtifactCompare = vi.fn();
    const withArtifacts: ChatUIMessage = {
      ...ASSISTANT_MSG,
      toolCalls: ["artifact-1", "artifact-2"].map((id, index) => ({
        id: `tool-${index}`,
        name: "search_codebase",
        arguments: {},
        status: "done" as const,
        artifact: {
          id,
          version: 1 as const,
          type: "search_results",
          tool_name: "search_codebase",
          title: index === 0 ? "First result" : "Second result",
          presentation: "search_results",
          evidence: { basis: "unknown" as const },
          data: { query: "chat", results: [] },
        },
      })),
    };
    render(<ChatInterface repoId="r1" messages={[withArtifacts]} activeArtifactId="artifact-1" compareArtifactId="artifact-2" onArtifactCompare={onArtifactCompare} isStreaming={false} onSend={vi.fn()} onCancel={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: "Second result" }));
    expect(onArtifactCompare).toHaveBeenCalledWith(null);
  });

  it("renders empty-state heading + suggestion chips when messages is empty", () => {
    render(
      <ChatInterface
        repoId="r1"
        repoName="acme"
        messages={[]}
        isStreaming={false}
        onSend={vi.fn()}
        onCancel={vi.fn()}
      />,
    );
    expect(screen.getByText(/Ask anything about acme/i)).toBeInTheDocument();
    expect(
      screen.getByText(/Give me an overview of this codebase/i),
    ).toBeInTheDocument();
  });

  it("renders messages and hides empty-state when transcript is non-empty", () => {
    render(
      <ChatInterface
        repoId="r1"
        messages={[USER_MSG, ASSISTANT_MSG]}
        isStreaming={false}
        onSend={vi.fn()}
        onCancel={vi.fn()}
      />,
    );
    expect(screen.getByText("Give me an overview")).toBeInTheDocument();
    expect(screen.getByText(/Hello — here is an overview\./)).toBeInTheDocument();
    expect(screen.queryByText(/Ask anything about/i)).not.toBeInTheDocument();
  });

  it("does not scroll the transcript on streaming message updates", () => {
    const scrollIntoView = vi.fn();
    Object.defineProperty(Element.prototype, "scrollIntoView", {
      configurable: true,
      value: scrollIntoView,
    });
    const streaming = { ...ASSISTANT_MSG, isStreaming: true, text: "First" };
    const view = render(
      <ChatInterface
        repoId="r1"
        messages={[USER_MSG, streaming]}
        isStreaming
        onSend={vi.fn()}
        onCancel={vi.fn()}
      />,
    );
    scrollIntoView.mockClear();

    view.rerender(
      <ChatInterface
        repoId="r1"
        messages={[USER_MSG, { ...streaming, text: "First second" }]}
        isStreaming
        onSend={vi.fn()}
        onCancel={vi.fn()}
      />,
    );

    expect(scrollIntoView).not.toHaveBeenCalled();
  });

  it("passes explicit page and dock density profiles to transcript turns", () => {
    const view = render(
      <ChatInterface
        repoId="r1"
        messages={[USER_MSG, ASSISTANT_MSG]}
        isStreaming={false}
        onSend={vi.fn()}
        onCancel={vi.fn()}
      />,
    );
    expect(screen.getByRole("article", { name: "Repowise" })).toHaveAttribute(
      "data-chat-density",
      "page",
    );

    view.rerender(
      <ChatInterface
        variant="dock"
        repoId="r1"
        messages={[USER_MSG, ASSISTANT_MSG]}
        isStreaming={false}
        onSend={vi.fn()}
        onCancel={vi.fn()}
      />,
    );
    expect(screen.getByRole("article", { name: "Repowise" })).toHaveAttribute(
      "data-chat-density",
      "dock",
    );
  });

  it("invokes onSend with trimmed text when the user submits", () => {
    const onSend = vi.fn();
    render(
      <ChatInterface
        repoId="r1"
        messages={[]}
        isStreaming={false}
        onSend={onSend}
        onCancel={vi.fn()}
      />,
    );
    const ta = screen.getByLabelText("Chat message") as HTMLTextAreaElement;
    fireEvent.change(ta, { target: { value: "  hi there  " } });
    fireEvent.click(screen.getByRole("button", { name: /send message/i }));
    expect(onSend).toHaveBeenCalledWith("hi there");
  });

  it("uses contextual placeholder, suggestions, and current-view metadata", () => {
    render(
      <ChatInterface
        repoId="r1"
        messages={[]}
        isStreaming={false}
        onSend={vi.fn()}
        onCancel={vi.fn()}
        context={{
          kind: "file",
          label: "Files",
          target: "packages/core/src/index.ts",
          targetKind: "path",
        }}
      />,
    );

    expect(screen.getByLabelText("Chat message")).toHaveAttribute(
      "placeholder",
      "Ask about this file",
    );
    expect(screen.getByText("Explain this file's responsibility")).toBeInTheDocument();
    expect(screen.getByText("Current view")).toBeInTheDocument();
    expect(screen.getByText("packages/core/src/index.ts")).toBeInTheDocument();
  });

  it("renders Stop button and invokes onCancel while streaming", () => {
    const onCancel = vi.fn();
    render(
      <ChatInterface
        repoId="r1"
        messages={[USER_MSG, { ...ASSISTANT_MSG, isStreaming: true, text: "" }]}
        isStreaming={true}
        onSend={vi.fn()}
        onCancel={onCancel}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /stop generation/i }));
    expect(onCancel).toHaveBeenCalled();
  });

  it("renders history in the header and model choice in the composer footer", () => {
    render(
      <ChatInterface
        repoId="r1"
        messages={[USER_MSG, ASSISTANT_MSG]}
        isStreaming={false}
        onSend={vi.fn()}
        onCancel={vi.fn()}
        modelSelectorSlot={<div data-testid="model-slot">model</div>}
        historySlot={<div data-testid="history-slot">history</div>}
      />,
    );
    expect(screen.getByTestId("model-slot")).toBeInTheDocument();
    expect(screen.getByTestId("history-slot")).toBeInTheDocument();
    const model = screen.getByTestId("model-slot");
    const shortcut = screen.getByText("Shift+Enter for newline");
    expect(model.compareDocumentPosition(shortcut) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(screen.getByRole("button", { name: "Send message" }).className).not.toContain("accent-fill");
  });

  it("announces meaningful stream transitions without announcing token deltas", () => {
    const streaming = { ...ASSISTANT_MSG, isStreaming: true, text: "First" };
    const view = render(
      <ChatInterface repoId="r1" messages={[USER_MSG, ASSISTANT_MSG]} isStreaming={false} onSend={vi.fn()} onCancel={vi.fn()} />,
    );
    view.rerender(
      <ChatInterface repoId="r1" messages={[USER_MSG, streaming]} isStreaming onSend={vi.fn()} onCancel={vi.fn()} />,
    );
    expect(screen.getByRole("status")).toHaveTextContent("Working on your answer.");
    view.rerender(
      <ChatInterface repoId="r1" messages={[USER_MSG, { ...ASSISTANT_MSG, text: "First second" }]} isStreaming={false} onSend={vi.fn()} onCancel={vi.fn()} />,
    );
    expect(screen.getByRole("status")).toHaveTextContent("Answer complete.");
    expect(screen.getByRole("status")).not.toHaveTextContent("First second");
  });
});
