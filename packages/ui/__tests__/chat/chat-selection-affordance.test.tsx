import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import type { ChatContext, ChatHandoff } from "@repowise-dev/types/chat";
import {
  ChatHandoffProvider,
  ChatSelectionAffordance,
} from "../../src/chat/index.js";

const PAGE: ChatContext = { kind: "overview", label: "Overview" };

function resolveContext(path?: string): ChatContext {
  return path
    ? { kind: "file", label: path, target: path, targetKind: "path" }
    : PAGE;
}

function setup(onHandoff: (handoff: ChatHandoff) => void, selectionEnabled = true) {
  return render(
    <ChatHandoffProvider
      onHandoff={onHandoff}
      selectionEnabled={selectionEnabled}
    >
      <div data-chat-selection="" data-chat-selection-path="a.py">
        <span data-line="41">const value = 1;</span>
        <span data-line="42">const other = 2;</span>
        <span data-line="43">const third = 3;</span>
      </div>
      <p data-testid="outside">Plain page copy.</p>
      <ChatSelectionAffordance resolveContext={resolveContext} />
    </ChatHandoffProvider>,
  );
}

/** jsdom computes no layout, so ranges report a zero rect. Give the control
 *  something to place itself against. */
function selectWithin(node: Node, endNode?: Node) {
  const range = document.createRange();
  if (endNode) {
    range.setStartBefore(node);
    range.setEndAfter(endNode);
  } else {
    range.selectNodeContents(node);
  }
  range.getClientRects = () =>
    [{ top: 100, left: 50, width: 120, height: 16 }] as unknown as DOMRectList;
  const selection = window.getSelection();
  selection?.removeAllRanges();
  selection?.addRange(range);
  fireEvent.mouseUp(document);
}

describe("ChatSelectionAffordance", () => {
  beforeEach(() => window.getSelection()?.removeAllRanges());

  it("renders nothing until something is selected", () => {
    setup(vi.fn());
    expect(screen.queryByRole("button", { name: "Ask about selection" })).toBeNull();
  });

  it("appears for a selection inside a marked region", () => {
    const view = setup(vi.fn());
    selectWithin(view.container.querySelector("[data-chat-selection]")!);
    expect(
      screen.getByRole("button", { name: "Ask about selection" }),
    ).toBeInTheDocument();
  });

  it("stays away from text outside a marked region", () => {
    const view = setup(vi.fn());
    selectWithin(view.getByTestId("outside"));
    expect(screen.queryByRole("button", { name: "Ask about selection" })).toBeNull();
  });

  it("carries the text, the path and the line range", () => {
    const onHandoff = vi.fn();
    const view = setup(onHandoff);
    selectWithin(view.container.querySelector("[data-chat-selection]")!);

    fireEvent.click(screen.getByRole("button", { name: "Ask about selection" }));

    expect(onHandoff).toHaveBeenCalledTimes(1);
    const handoff = onHandoff.mock.calls[0]![0] as ChatHandoff;
    expect(handoff.selection?.path).toBe("a.py");
    expect(handoff.selection?.startLine).toBe(41);
    expect(handoff.selection?.endLine).toBe(43);
    expect(handoff.context).toEqual({
      kind: "file",
      label: "a.py",
      target: "a.py",
      targetKind: "path",
    });
  });

  it("names only the lines the selection actually covers", () => {
    // The bug this guards: reading the boundary containers named the region's
    // first and last line whatever the reader picked, because a boundary
    // normalizes to a parent element on a triple-click.
    const onHandoff = vi.fn();
    const view = setup(onHandoff);
    const lines = view.container.querySelectorAll("[data-line]");
    selectWithin(lines[1]!, lines[1]!);

    fireEvent.click(screen.getByRole("button", { name: "Ask about selection" }));

    const handoff = onHandoff.mock.calls[0]![0] as ChatHandoff;
    expect(handoff.selection?.startLine).toBe(42);
    expect(handoff.selection?.endLine).toBe(42);
  });

  it("reports no range at all where the DOM carries no line numbers", () => {
    const onHandoff = vi.fn();
    const view = render(
      <ChatHandoffProvider onHandoff={onHandoff}>
        <div data-chat-selection="">
          <pre>some fenced snippet</pre>
        </div>
        <ChatSelectionAffordance resolveContext={resolveContext} />
      </ChatHandoffProvider>,
    );
    selectWithin(view.container.querySelector("pre")!);

    fireEvent.click(screen.getByRole("button", { name: "Ask about selection" }));

    const handoff = onHandoff.mock.calls[0]![0] as ChatHandoff;
    expect(handoff.selection?.startLine).toBeUndefined();
    expect(handoff.selection?.endLine).toBeUndefined();
  });

  it("renders nothing when the reader has switched it off", () => {
    const view = setup(vi.fn(), false);
    selectWithin(view.container.querySelector("[data-chat-selection]")!);
    expect(screen.queryByRole("button", { name: "Ask about selection" })).toBeNull();
  });

  it("goes away when the selection collapses", () => {
    const view = setup(vi.fn());
    selectWithin(view.container.querySelector("[data-chat-selection]")!);
    expect(screen.getByRole("button", { name: "Ask about selection" })).toBeInTheDocument();

    window.getSelection()?.removeAllRanges();
    fireEvent(document, new Event("selectionchange"));
    expect(screen.queryByRole("button", { name: "Ask about selection" })).toBeNull();
  });
});
