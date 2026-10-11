import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { ChatInterface } from "../../src/chat/chat-interface.js";
import { ChatDock, type ChatDockProps } from "../../src/chat/chat-dock.js";
import type { ChatSuggestion, ChatUIMessage } from "@repowise-dev/types/chat";

const FOLLOW_UPS: ChatSuggestion[] = [
  { text: "Which tests cover incremental.py?", source: "followup", toolHint: "get_risk" },
  { text: "What changed there most recently?", source: "followup" },
];

const ANSWER: ChatUIMessage = {
  id: "asst-1",
  role: "assistant",
  text: "incremental.py carries 28 recorded bug fixes.",
  toolCalls: [],
  isStreaming: false,
  followUps: FOLLOW_UPS,
};

const QUESTION: ChatUIMessage = {
  id: "user-1",
  role: "user",
  text: "What is risky about incremental.py?",
  toolCalls: [],
  isStreaming: false,
};

function interfaceProps(messages: ChatUIMessage[]) {
  return {
    repoId: "r1",
    messages,
    isStreaming: false,
    onSend: vi.fn(),
    onCancel: vi.fn(),
  };
}

afterEach(cleanup);

describe("follow-ups in the transcript", () => {
  it("offers the newest answer's next steps", () => {
    render(<ChatInterface {...interfaceProps([QUESTION, ANSWER])} />);
    const group = screen.getByRole("group", { name: "Next steps" });
    expect(group).toBeInTheDocument();
    for (const followUp of FOLLOW_UPS) {
      expect(screen.getByRole("button", { name: followUp.text })).toBeInTheDocument();
    }
  });

  it("seeds the composer with the chip's question", () => {
    render(<ChatInterface {...interfaceProps([QUESTION, ANSWER])} />);
    fireEvent.click(screen.getByRole("button", { name: FOLLOW_UPS[0]!.text }));
    expect(screen.getByRole("textbox")).toHaveValue(FOLLOW_UPS[0]!.text);
  });

  it("stays out of the way of a question the reader is already writing", () => {
    render(
      <ChatInterface
        {...interfaceProps([QUESTION, ANSWER])}
        draft="half a question"
        onDraftChange={vi.fn()}
      />,
    );
    expect(screen.queryByRole("group", { name: "Next steps" })).not.toBeInTheDocument();
  });

  it("never follows an error", () => {
    render(
      <ChatInterface {...interfaceProps([QUESTION, ANSWER])} error="Provider refused" />,
    );
    expect(screen.queryByRole("group", { name: "Next steps" })).not.toBeInTheDocument();
  });

  it("waits for the answer to finish", () => {
    render(
      <ChatInterface
        {...interfaceProps([QUESTION, { ...ANSWER, isStreaming: true }])}
        isStreaming
      />,
    );
    expect(screen.queryByRole("group", { name: "Next steps" })).not.toBeInTheDocument();
  });

  it("offers only the newest answer's, not every past turn's", () => {
    const older: ChatUIMessage = {
      ...ANSWER,
      id: "asst-0",
      followUps: [{ text: "A stale next step", source: "followup" }],
    };
    render(<ChatInterface {...interfaceProps([QUESTION, older, QUESTION, ANSWER])} />);
    expect(screen.queryByRole("button", { name: "A stale next step" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: FOLLOW_UPS[0]!.text })).toBeInTheDocument();
  });
});

function dockProps(overrides: Partial<ChatDockProps> = {}): ChatDockProps {
  return {
    storageKey: "dock:suggestions",
    repoId: "r1",
    context: { kind: "file", label: "Files", target: "src/a.py", targetKind: "path" },
    messages: [],
    isStreaming: false,
    onSend: vi.fn(),
    onCancel: vi.fn(),
    ...overrides,
  };
}

function openDock() {
  fireEvent.click(screen.getByRole("button", { name: "Open repository chat" }));
}

describe("the compact dock's chip slot", () => {
  beforeEach(() => window.localStorage.clear());

  it("falls back to the page kind's static tier", () => {
    render(<ChatDock {...dockProps()} />);
    openDock();
    expect(
      screen.getByRole("button", { name: "Explain this file's responsibility" }),
    ).toHaveAttribute("data-chat-suggestion", "static");
  });

  it("prefers suggestions measured from the page", () => {
    const measured: ChatSuggestion[] = [
      { text: "Explain the 28 bug fixes in src/a.py", source: "page" },
    ];
    render(<ChatDock {...dockProps({ suggestions: measured })} />);
    openDock();
    expect(
      screen.getByRole("button", { name: "Explain the 28 bug fixes in src/a.py" }),
    ).toHaveAttribute("data-chat-suggestion", "page");
    expect(
      screen.queryByRole("button", { name: "Explain this file's responsibility" }),
    ).not.toBeInTheDocument();
  });

  it("turns the slot over to the last answer's next steps once a conversation starts", () => {
    render(<ChatDock {...dockProps({ messages: [QUESTION, ANSWER] })} />);
    openDock();
    expect(
      screen.queryByRole("button", { name: "Explain this file's responsibility" }),
    ).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: FOLLOW_UPS[0]!.text })).toBeInTheDocument();
  });

  it("seeds the composer instead of sending, so the reader can edit first", () => {
    const onSend = vi.fn();
    render(<ChatDock {...dockProps({ onSend })} />);
    openDock();
    fireEvent.click(
      screen.getByRole("button", { name: "Explain this file's responsibility" }),
    );
    expect(onSend).not.toHaveBeenCalled();
    expect(screen.getByRole("textbox")).toHaveValue(
      "Explain this file's responsibility",
    );
  });

  it("clears the slot while a draft is being written", () => {
    render(<ChatDock {...dockProps()} />);
    openDock();
    fireEvent.change(screen.getByRole("textbox"), { target: { value: "my own" } });
    expect(
      screen.queryByRole("button", { name: "Explain this file's responsibility" }),
    ).not.toBeInTheDocument();
  });
});
