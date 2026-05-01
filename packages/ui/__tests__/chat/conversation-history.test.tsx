import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { ConversationHistory } from "../../src/chat/conversation-history.js";
import type { Conversation } from "@repowise-dev/types/chat";

const CONVERSATIONS: Conversation[] = [
  {
    id: "c1",
    repository_id: "r1",
    title: "Auth flow review",
    message_count: 4,
    created_at: new Date(Date.now() - 60_000).toISOString(),
    updated_at: new Date(Date.now() - 60_000).toISOString(),
  },
  {
    id: "c2",
    repository_id: "r1",
    title: "Dead code triage",
    message_count: 2,
    created_at: new Date(Date.now() - 7_200_000).toISOString(),
    updated_at: new Date(Date.now() - 7_200_000).toISOString(),
  },
];

describe("ConversationHistory shell", () => {
  it("renders the history trigger button", () => {
    render(
      <ConversationHistory
        conversations={CONVERSATIONS}
        onSelect={vi.fn()}
        onDelete={vi.fn()}
        onNew={vi.fn()}
      />,
    );
    expect(
      screen.getByRole("button", { name: /history/i }),
    ).toBeInTheDocument();
  });

  it("opens dropdown and lists conversations on click", () => {
    render(
      <ConversationHistory
        conversations={CONVERSATIONS}
        onSelect={vi.fn()}
        onDelete={vi.fn()}
        onNew={vi.fn()}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /history/i }));
    expect(screen.getByText("Auth flow review")).toBeInTheDocument();
    expect(screen.getByText("Dead code triage")).toBeInTheDocument();
    expect(screen.getByText(/New conversation/i)).toBeInTheDocument();
  });

  it("forwards onSelect when a conversation row is clicked", () => {
    const onSelect = vi.fn();
    render(
      <ConversationHistory
        conversations={CONVERSATIONS}
        onSelect={onSelect}
        onDelete={vi.fn()}
        onNew={vi.fn()}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /history/i }));
    fireEvent.click(screen.getByText("Auth flow review"));
    expect(onSelect).toHaveBeenCalledWith("c1");
  });

  it("forwards onDelete when the row trash icon is clicked", () => {
    const onDelete = vi.fn();
    render(
      <ConversationHistory
        conversations={CONVERSATIONS}
        onSelect={vi.fn()}
        onDelete={onDelete}
        onNew={vi.fn()}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /history/i }));
    const deleteButtons = screen.getAllByRole("button", {
      name: /delete conversation/i,
    });
    fireEvent.click(deleteButtons[0]!);
    expect(onDelete).toHaveBeenCalledWith("c1");
  });

  it("renders the empty placeholder when conversations is an empty list", () => {
    render(
      <ConversationHistory
        conversations={[]}
        onSelect={vi.fn()}
        onDelete={vi.fn()}
        onNew={vi.fn()}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /history/i }));
    expect(screen.getByText(/No conversations yet/i)).toBeInTheDocument();
  });
});
