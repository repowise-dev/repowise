import { describe, it, expect } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { ToolCallGroup } from "../../src/chat/tool-call-group.js";
import type { ChatUIToolCall } from "@repowise-dev/types/chat";

function call(id: string, status: ChatUIToolCall["status"]): ChatUIToolCall {
  return {
    id,
    name: "search_codebase",
    arguments: { query: "auth" },
    status,
    ...(status === "done" ? { result: { results: [] } } : {}),
  };
}

/** Every element carrying the group shell's ground. */
function shells(container: HTMLElement) {
  return container.querySelectorAll('[data-activity-trail="true"]');
}

describe("ToolCallGroup", () => {
  it("renders one container for a lone call, not a box inside a box", () => {
    const { container } = render(<ToolCallGroup toolCalls={[call("a", "done")]} />);
    expect(shells(container)).toHaveLength(1);
  });

  it("keeps a single container when the group is expanded", () => {
    // Steps used to be bordered `bg-elevated` boxes nested inside the group's
    // bordered `bg-elevated` box — the same plane twice, once per step.
    const { container } = render(
      <ToolCallGroup
        toolCalls={[call("a", "done"), call("b", "done"), call("c", "done")]}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /Activity/ }));
    expect(shells(container)).toHaveLength(1);
  });

  it("uses one calm working orb rather than orange spinners", () => {
    // Rule 10: a badge every row carries says nothing. Success is the default,
    // so only work in flight gets a marker.
    const { container: done } = render(
      <ToolCallGroup toolCalls={[call("a", "done")]} />,
    );
    expect(done.querySelector(".animate-spin")).toBeNull();
    expect(done.querySelector('[class*="color-success"]')).toBeNull();

    const { container: running } = render(
      <ToolCallGroup toolCalls={[call("b", "running")]} />,
    );
    expect(running.querySelector(".animate-spin")).toBeNull();
    expect(running.querySelector('[data-working-orb="true"]')).not.toBeNull();
    expect(running.innerHTML).not.toContain("color-accent-primary");
  });

  it("auto-expands while a step is running and reports the step count", () => {
    render(
      <ToolCallGroup toolCalls={[call("a", "done"), call("b", "running")]} />,
    );
    expect(screen.getByText(/Working/)).toBeInTheDocument();
    expect(screen.getByText(/2 steps/)).toBeInTheDocument();
    // Expanded without a click: both step labels are on screen.
    expect(screen.getAllByText("Searching codebase")).toHaveLength(2);
  });
});
