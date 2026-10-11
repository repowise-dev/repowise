import { describe, expect, it, vi } from "vitest";
import { fireEvent, render } from "@testing-library/react";
import { useChatShortcut } from "../../src/chat/use-chat-shortcut.js";

function Probe({ onTrigger, enabled }: { onTrigger: () => void; enabled?: boolean }) {
  useChatShortcut(onTrigger, enabled);
  return (
    <div>
      <input aria-label="filter" />
      <textarea aria-label="composer" />
      <div contentEditable aria-label="note" />
      <div role="combobox" aria-label="picker" />
      <div role="textbox" aria-label="rich" />
    </div>
  );
}

describe("useChatShortcut", () => {
  it("fires on a bare ?", () => {
    const onTrigger = vi.fn();
    render(<Probe onTrigger={onTrigger} />);
    fireEvent.keyDown(window, { key: "?" });
    expect(onTrigger).toHaveBeenCalledTimes(1);
  });

  it("ignores ? with a platform modifier, which belongs to the browser", () => {
    const onTrigger = vi.fn();
    render(<Probe onTrigger={onTrigger} />);
    fireEvent.keyDown(window, { key: "?", metaKey: true });
    fireEvent.keyDown(window, { key: "?", ctrlKey: true });
    fireEvent.keyDown(window, { key: "?", altKey: true });
    expect(onTrigger).not.toHaveBeenCalled();
  });

  it("stays out of the way while the reader is typing", () => {
    const onTrigger = vi.fn();
    const view = render(<Probe onTrigger={onTrigger} />);
    for (const label of ["filter", "composer", "note", "picker", "rich"]) {
      fireEvent.keyDown(view.getByLabelText(label), { key: "?", bubbles: true });
    }
    expect(onTrigger).not.toHaveBeenCalled();
  });

  it("does not claim any other key", () => {
    const onTrigger = vi.fn();
    render(<Probe onTrigger={onTrigger} />);
    for (const key of ["/", "k", "Escape", "Enter"]) {
      fireEvent.keyDown(window, { key });
    }
    expect(onTrigger).not.toHaveBeenCalled();
  });

  it("registers nothing while disabled", () => {
    const onTrigger = vi.fn();
    render(<Probe onTrigger={onTrigger} enabled={false} />);
    fireEvent.keyDown(window, { key: "?" });
    expect(onTrigger).not.toHaveBeenCalled();
  });

  it("keeps one listener across renders when the caller is not memoized", () => {
    const first = vi.fn();
    const second = vi.fn();
    const view = render(<Probe onTrigger={first} />);
    view.rerender(<Probe onTrigger={second} />);

    fireEvent.keyDown(window, { key: "?" });

    expect(first).not.toHaveBeenCalled();
    expect(second).toHaveBeenCalledTimes(1);
  });

  it("stops listening once unmounted", () => {
    const onTrigger = vi.fn();
    const view = render(<Probe onTrigger={onTrigger} />);
    view.unmount();
    fireEvent.keyDown(window, { key: "?" });
    expect(onTrigger).not.toHaveBeenCalled();
  });
});
