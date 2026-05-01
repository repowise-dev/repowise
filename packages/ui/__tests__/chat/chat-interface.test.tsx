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

  it("renders modelSelectorSlot + historySlot in the active-conversation header", () => {
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
  });
});
