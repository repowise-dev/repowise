import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { ChatSuggestions } from "../../src/chat/chat-suggestions.js";
import { getChatContextPresentation } from "../../src/chat/chat-context.js";
import type { ChatSuggestion } from "@repowise-dev/types/chat";

const PAGE: ChatSuggestion[] = [
  { text: "Explain the 28 bug fixes in incremental.py", source: "page", toolHint: "get_risk" },
  { text: "Which tests cover incremental.py?", source: "page" },
];

afterEach(cleanup);

describe("the static fallback tier", () => {
  it("carries every presentation string as a static suggestion", () => {
    const presentation = getChatContextPresentation({
      kind: "symbol",
      label: "Symbols",
      target: "useChat",
    });
    expect(presentation.suggestions.length).toBeGreaterThan(0);
    expect(presentation.suggestions.every((s) => s.source === "static")).toBe(true);
    expect(presentation.suggestions.map((s) => s.text)).toContain(
      "Explain what this symbol does",
    );
  });

  it("keeps the collection tier separate from the targeted tier", () => {
    const collection = getChatContextPresentation({ kind: "file", label: "Files" });
    const targeted = getChatContextPresentation({
      kind: "file",
      label: "Files",
      target: "src/index.ts",
    });
    expect(collection.suggestions.map((s) => s.text)).toContain(
      "Which files are the main entry points?",
    );
    expect(targeted.suggestions.map((s) => s.text)).toContain(
      "Explain this file's responsibility",
    );
    expect(collection.suggestions.every((s) => s.source === "static")).toBe(true);
  });
});

describe("ChatSuggestions", () => {
  it("renders nothing when there is nothing to suggest", () => {
    const { container } = render(
      <ChatSuggestions suggestions={[]} onSelect={vi.fn()} layout="chips" />,
    );
    expect(container).toBeEmptyDOMElement();
  });

  it("hands the whole suggestion back, not just its text", () => {
    const onSelect = vi.fn();
    render(<ChatSuggestions suggestions={PAGE} onSelect={onSelect} layout="chips" />);
    fireEvent.click(screen.getByRole("button", { name: PAGE[0]!.text }));
    expect(onSelect).toHaveBeenCalledWith(PAGE[0]);
  });

  it("renders every suggestion as a keyboard-reachable button in both layouts", () => {
    const { rerender } = render(
      <ChatSuggestions suggestions={PAGE} onSelect={vi.fn()} layout="chips" />,
    );
    expect(screen.getAllByRole("button")).toHaveLength(2);

    rerender(
      <ChatSuggestions
        suggestions={PAGE}
        onSelect={vi.fn()}
        layout="rows"
        label="Start with"
      />,
    );
    const rows = screen.getAllByRole("button");
    expect(rows).toHaveLength(2);
    for (const row of rows) {
      expect(row).not.toHaveAttribute("disabled");
      expect(row.tabIndex).not.toBe(-1);
    }
    expect(screen.getByText("Start with")).toBeInTheDocument();
  });

  it("marks each chip with its source so a page tier is distinguishable", () => {
    render(
      <ChatSuggestions
        suggestions={[
          { text: "measured", source: "page" },
          { text: "generic", source: "static" },
        ]}
        onSelect={vi.fn()}
        layout="chips"
      />,
    );
    expect(screen.getByRole("button", { name: "measured" })).toHaveAttribute(
      "data-chat-suggestion",
      "page",
    );
    expect(screen.getByRole("button", { name: "generic" })).toHaveAttribute(
      "data-chat-suggestion",
      "static",
    );
  });

  it("names the chip group for a screen reader, which has no visible heading", () => {
    render(
      <ChatSuggestions
        suggestions={PAGE}
        onSelect={vi.fn()}
        layout="chips"
        ariaLabel="Next steps"
      />,
    );
    expect(screen.getByRole("group", { name: "Next steps" })).toBeInTheDocument();
  });
});
