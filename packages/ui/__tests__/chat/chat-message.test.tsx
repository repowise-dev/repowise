import { describe, it, expect } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
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

  it("renders the user on the opposite side as a borderless bubble with no label or avatar", () => {
    const { container } = render(<ChatMessage message={USER} repoId="r1" />);
    const turn = screen.getByRole("article", { name: "You" });
    expect(turn.className).toContain("items-end");
    expect(turn).toHaveAttribute("data-chat-role", "user");
    expect(screen.queryByText("You")).toBeNull();
    expect(container.querySelector("img, svg.lucide-user-round")).toBeNull();
    const bubble = screen.getByText("Where is auth handled?");
    expect(bubble.className).toContain("bg-[var(--color-bg-elevated)]");
    expect(bubble.className).not.toContain("border");
  });

  it("renders the answer without a label or avatar", () => {
    const { container } = render(<ChatMessage message={ASSISTANT} repoId="r1" />);
    expect(screen.queryByText("Repowise")).toBeNull();
    expect(container.querySelector("img")).toBeNull();
  });

  it("names the model only when it changed from the previous answer", () => {
    const withModel = { ...ASSISTANT, provider: "anthropic", model: "claude" };
    const view = render(<ChatMessage message={withModel} repoId="r1" />);
    expect(screen.queryByText(/anthropic/)).toBeNull();
    view.rerender(<ChatMessage message={withModel} repoId="r1" modelChanged />);
    expect(screen.getByText(/Model changed to anthropic · claude/)).toBeInTheDocument();
  });

  it("offers copy and retry, with no canned follow up", () => {
    render(<ChatMessage message={ASSISTANT} repoId="r1" isLatest onRetry={() => {}} />);
    expect(screen.getByRole("button", { name: "Copy" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Retry" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /follow up/i })).toBeNull();
    // The newest answer never hides its actions behind hover.
    const group = screen.getByRole("group", { name: "assistant message actions" });
    expect(group.className).not.toContain("opacity-0");
  });

  it("reveals actions on hover or focus for older turns, never only on hover for touch", () => {
    render(<ChatMessage message={ASSISTANT} repoId="r1" />);
    const group = screen.getByRole("group", { name: "assistant message actions" });
    expect(group.className).toContain("pointer-fine:opacity-0");
    expect(group.className).toContain("group-focus-within/turn:opacity-100");
  });

  it("shows an inline error with Retry when the answer failed before any text", () => {
    let retried = 0;
    render(
      <ChatMessage
        message={{ ...ASSISTANT, text: "" }}
        repoId="r1"
        error="The provider rejected the request."
        onRetry={() => {
          retried += 1;
        }}
      />,
    );
    expect(screen.getByRole("alert")).toHaveTextContent("The provider rejected the request.");
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(retried).toBe(1);
  });

  it("resends an edit with the primary action, not the model colour", () => {
    render(
      <ChatMessage
        message={{ ...USER, serverId: "s1" }}
        repoId="r1"
        onEditAndResend={() => {}}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "Edit and resend" }));
    const submit = screen.getByRole("button", { name: "Fork and resend" });
    expect(submit.className).toContain("accent-fill");
    expect(submit.className).not.toContain("color-model");
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
