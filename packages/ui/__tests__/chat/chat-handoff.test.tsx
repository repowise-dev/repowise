import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import type { ChatContext, ChatHandoff } from "@repowise-dev/types/chat";
import {
  AskAboutThis,
  ChatHandoffProvider,
  buildHandoffDraft,
} from "../../src/chat/index.js";

const CONTEXT: ChatContext = {
  kind: "file",
  label: "packages/core/parser.py",
  target: "packages/core/parser.py",
  targetKind: "path",
};

function renderControl(
  onHandoff: (handoff: ChatHandoff) => void,
  askEnabled = true,
) {
  return render(
    <ChatHandoffProvider onHandoff={onHandoff} askEnabled={askEnabled}>
      <AskAboutThis context={CONTEXT} question="What is this for?" />
    </ChatHandoffProvider>,
  );
}

describe("AskAboutThis", () => {
  it("hands the page's context and question to the host", () => {
    const onHandoff = vi.fn();
    renderControl(onHandoff);

    fireEvent.click(
      screen.getByRole("button", { name: "Ask about packages/core/parser.py" }),
    );

    expect(onHandoff).toHaveBeenCalledWith({
      context: CONTEXT,
      question: "What is this for?",
    });
  });

  it("renders nothing when the reader has switched the controls off", () => {
    renderControl(vi.fn(), false);
    expect(screen.queryByRole("button")).toBeNull();
  });

  it("renders nothing outside a chat host", () => {
    render(<AskAboutThis context={CONTEXT} question="What is this for?" />);
    expect(screen.queryByRole("button")).toBeNull();
  });

  it("names the control so a screen reader announces the subject", () => {
    renderControl(vi.fn());
    const button = screen.getByRole("button", {
      name: "Ask about packages/core/parser.py",
    });
    expect(button.getAttribute("aria-label")).toBe(
      "Ask about packages/core/parser.py",
    );
  });
});

describe("buildHandoffDraft", () => {
  it("is the question alone when nothing was selected", () => {
    expect(buildHandoffDraft({ context: CONTEXT, question: "Why?" })).toBe("Why?");
  });

  it("quotes a selection with its file and line range", () => {
    const draft = buildHandoffDraft({
      context: CONTEXT,
      question: "Explain this.",
      selection: {
        text: "def parse():",
        path: "packages/core/parser.py",
        startLine: 10,
        endLine: 12,
      },
    });

    expect(draft).toContain("Explain this.");
    expect(draft).toContain("From packages/core/parser.py:10-12:");
    expect(draft).toContain("def parse():");
  });

  it("names one line rather than a range of one", () => {
    const draft = buildHandoffDraft({
      context: CONTEXT,
      selection: { text: "x = 1", path: "a.py", startLine: 4, endLine: 4 },
    });
    expect(draft).toContain("From a.py:4:");
  });

  it("outruns a fence inside the selection so the quote cannot close early", () => {
    const draft = buildHandoffDraft({
      context: CONTEXT,
      question: "What does this show?",
      selection: { text: "```py\nx = 1\n```", path: "a.md" },
    });

    const fences = draft.match(/^`{4,}$/gm) ?? [];
    expect(fences).toHaveLength(2);
    expect(draft).toContain("```py");
  });

  it("says so when it truncates a long selection", () => {
    const draft = buildHandoffDraft({
      context: CONTEXT,
      selection: { text: "x".repeat(5000), path: "a.py" },
    });
    expect(draft).toContain("first 2000 characters");
    expect(draft.length).toBeLessThan(2300);
  });
});
