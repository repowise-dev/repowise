import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { ChatMessage } from "../../src/chat/chat-message.js";
import type { ChatUIMessage } from "@repowise-dev/types/chat";

const USER: ChatUIMessage = {
  id: "u1",
  role: "user",
  text: "Where is auth handled?",
  toolCalls: [],
  isStreaming: false,
};

const ASSISTANT: ChatUIMessage = {
  id: "a1",
  role: "assistant",
  text: "In `src/auth/session.py`.",
  toolCalls: [],
  isStreaming: false,
};

describe("ChatMessage", () => {
  it("renders the user's turn without an accent ground", () => {
    // The question used to be a solid accent bubble: the loudest object on the
    // page, spent on the one element that does not respond to anything.
    const { container } = render(<ChatMessage message={USER} repoId="r1" />);
    expect(screen.getByText("Where is auth handled?")).toBeInTheDocument();
    expect(
      container.querySelector('[class*="bg-[var(--color-accent-primary)]"]'),
    ).toBeNull();
  });

  it("renders the user on the opposite side with a neutral identity surface", () => {
    render(<ChatMessage message={USER} repoId="r1" />);
    const turn = screen.getByRole("article", { name: "You" });
    expect(turn.className).toContain("justify-end");
    expect(turn).toHaveAttribute("data-chat-role", "user");
    expect(screen.getByText("You")).toBeInTheDocument();
    expect(screen.getByText("Where is auth handled?").className).toContain(
      "bg-[var(--color-bg-surface)]",
    );
  });

  it("renders assistant text as markdown", () => {
    const { container } = render(
      <ChatMessage message={ASSISTANT} repoId="r1" />,
    );
    expect(container.querySelector("code")?.textContent).toBe(
      "src/auth/session.py",
    );
  });

  it("is memoised so a streaming tail does not re-render the transcript", () => {
    // Rule 16. Without the memo, every SSE token re-parses every prior reply
    // through react-markdown.
    expect(
      (ChatMessage as unknown as { $$typeof: symbol }).$$typeof,
    ).toBe(Symbol.for("react.memo"));
  });
});

describe("ChatMessage truncation", () => {
  it("shows a quiet row when the answer stopped at the step ceiling", () => {
    const { container } = render(
      <ChatMessage message={{ ...ASSISTANT, text: "", truncated: true }} repoId="r1" />,
    );
    const row = container.querySelector('[data-chat-truncated="true"]');
    expect(row).not.toBeNull();
    expect(row?.textContent).toMatch(/step limit/);
    expect(row?.className).not.toContain("color-error");
    expect(row?.className).not.toContain("color-accent-primary");
  });

  it("holds the row back while the turn is still streaming", () => {
    const { container } = render(
      <ChatMessage
        message={{ ...ASSISTANT, text: "", truncated: true, isStreaming: true }}
        repoId="r1"
      />,
    );
    expect(container.querySelector('[data-chat-truncated="true"]')).toBeNull();
  });

  it("renders nothing extra for a completed answer", () => {
    const { container } = render(<ChatMessage message={ASSISTANT} repoId="r1" />);
    expect(container.querySelector('[data-chat-truncated="true"]')).toBeNull();
  });

  it("keeps the working marker while a grounded turn waits for its first token", () => {
    const { container } = render(
      <ChatMessage
        message={{
          ...ASSISTANT,
          text: "",
          isStreaming: true,
          toolCalls: [
            {
              id: "grounding-1",
              name: "get_context",
              arguments: {},
              status: "done",
              origin: "grounding",
            },
          ],
        }}
        repoId="r1"
      />,
    );
    expect(container.querySelector('[data-working-orb="true"]')).not.toBeNull();
  });
});
