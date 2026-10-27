import { describe, it, expect } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { ToolCallGroup, summarizeToolCalls } from "../../src/chat/tool-call-group.js";
import { readableToolName } from "../../src/chat/tool-call-block.js";
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
    fireEvent.click(screen.getByRole("button", { name: /3 searches/ }));
    expect(shells(container)).toHaveLength(1);
  });

  it("marks no step with a spinner or success badge", () => {
    // Rule 10: a badge every row carries says nothing. The live marker is the
    // turn's stage orb, not one per tool row.
    const { container: done } = render(
      <ToolCallGroup toolCalls={[call("a", "done")]} />,
    );
    expect(done.querySelector(".animate-spin")).toBeNull();
    expect(done.querySelector('[class*="color-success"]')).toBeNull();

    const { container: running } = render(
      <ToolCallGroup toolCalls={[call("b", "running")]} />,
    );
    expect(running.querySelector(".animate-spin")).toBeNull();
    expect(running.querySelector('[data-working-orb="true"]')).toBeNull();
    expect(running.innerHTML).not.toContain("text-[var(--color-accent-primary)]");
  });

  it("summarises several steps in one quiet line without borders", () => {
    const { container } = render(
      <ToolCallGroup toolCalls={[call("a", "done"), call("b", "running")]} />,
    );
    const line = screen.getByRole("button", { name: /2 searches/ });
    expect(line).toHaveAttribute("aria-expanded", "false");
    expect(container.innerHTML).not.toContain("border");
    expect(container.innerHTML).not.toContain("uppercase");
    fireEvent.click(line);
    expect(screen.getAllByText("Searching codebase")).toHaveLength(2);
  });

  it("serialises input and result into the shared 12px code block only when opened", () => {
    const { container } = render(<ToolCallGroup toolCalls={[call("a", "done")]} />);
    expect(container.querySelector("pre")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: /Searching codebase/ }));
    expect(screen.getByText("Input")).toBeInTheDocument();
    expect(screen.getByText("Result")).toBeInTheDocument();
    expect(container.querySelector("[data-chat-selection]")?.className).toContain("text-xs");
    expect(container.querySelector("pre code")?.textContent).toContain('"query": "auth"');
  });

  it("names unknown tools readably", () => {
    expect(readableToolName("get_blast_radius")).toBe("Blast radius");
    render(<ToolCallGroup toolCalls={[{ ...call("a", "done"), name: "get_blast_radius" }]} />);
    expect(screen.getByText("Blast radius")).toBeInTheDocument();
  });
});

describe("summarizeToolCalls", () => {
  it("counts pages read, searches and checks", () => {
    expect(
      summarizeToolCalls([
        { id: "1", name: "get_context", arguments: { targets: ["a", "b", "c"] }, status: "done" },
        { id: "2", name: "get_symbol", arguments: {}, status: "done" },
        { id: "3", name: "search_codebase", arguments: {}, status: "done" },
        { id: "4", name: "search_codebase", arguments: {}, status: "error" },
        { id: "5", name: "get_health", arguments: {}, status: "done" },
      ]),
    ).toBe("Read 4 pages · 2 searches · 1 check · 1 failed");
  });
});

describe("ToolCallGroup grounding row", () => {
  const grounding: ChatUIToolCall = {
    id: "grounding-1",
    name: "get_context",
    arguments: { targets: ["src/a.py"] },
    summary: "src/a.py",
    status: "done",
    origin: "grounding",
    artifact: {
      id: "art-1",
      version: 1,
      type: "context",
      tool_name: "get_context",
      presentation: "context",
      data: { targets: { "src/a.py": {} } },
    },
  };

  it("renders a read made for the page as one quiet row naming what was read", () => {
    const { container } = render(<ToolCallGroup toolCalls={[grounding]} />);
    expect(shells(container)).toHaveLength(1);
    expect(screen.getByText("Read for this page")).toBeInTheDocument();
    expect(screen.getByText(/src\/a\.py/)).toBeInTheDocument();
    expect(container.querySelector('[data-tool-origin="grounding"]')).not.toBeNull();
    expect(container.querySelector('[data-working-orb="true"]')).toBeNull();
    expect(container.innerHTML).not.toContain("color-success");
  });

  it("keeps the model's own steps under their tool labels", () => {
    render(<ToolCallGroup toolCalls={[grounding, call("b", "done")]} />);
    fireEvent.click(screen.getByRole("button", { name: /Read 1 page · 1 search/ }));
    expect(screen.getByText("Read for this page")).toBeInTheDocument();
    expect(screen.getByText("Searching codebase")).toBeInTheDocument();
  });
});
