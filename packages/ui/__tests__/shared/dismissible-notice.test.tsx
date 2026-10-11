import { describe, it, expect, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";

import { DismissibleNotice } from "../../src/shared/dismissible-notice";

describe("DismissibleNotice", () => {
  it("announces as status, not as an alert", () => {
    render(<DismissibleNotice>Accounting changed.</DismissibleNotice>);
    // role="alert" is assertive and interrupts a screen reader mid-sentence.
    // A methodology note is news about numbers already on the page.
    expect(screen.getByRole("status")).toHaveTextContent("Accounting changed.");
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("renders no dismiss control when no handler is given", () => {
    render(<DismissibleNotice>Permanent note.</DismissibleNotice>);
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });

  it("calls onDismiss and leaves the notice rendered", () => {
    const onDismiss = vi.fn();
    render(<DismissibleNotice onDismiss={onDismiss}>Dismiss me.</DismissibleNotice>);

    fireEvent.click(screen.getByRole("button", { name: /dismiss/i }));

    expect(onDismiss).toHaveBeenCalledTimes(1);
    // The component is controlled: it does not hide itself, because whether
    // this notice comes back is a question only the host can answer. A version
    // that self-hid would pass a naive test and still reappear on reload.
    expect(screen.getByRole("status")).toBeInTheDocument();
  });

  it("activates the dismiss control from the keyboard", () => {
    const onDismiss = vi.fn();
    render(<DismissibleNotice onDismiss={onDismiss}>Keyboard.</DismissibleNotice>);
    const button = screen.getByRole("button", { name: /dismiss/i });

    // A real button, not a click-handling div: focusable, and Enter and Space
    // both fire it. Asserting the Tailwind class name instead would have
    // restated the implementation and passed against any styling at all.
    button.focus();
    expect(button).toHaveFocus();
    expect(button).toHaveAttribute("type", "button");

    fireEvent.click(button);
    expect(onDismiss).toHaveBeenCalledTimes(1);
  });

  it("puts the action ahead of the dismiss control in the tab order", () => {
    render(
      <DismissibleNotice onDismiss={vi.fn()} action={<a href="/method">See what changed</a>}>
        With an action.
      </DismissibleNotice>,
    );

    const link = screen.getByRole("link", { name: "See what changed" });
    const button = screen.getByRole("button", { name: /dismiss/i });

    // Neither carries a tabindex, so document order is tab order: a reader
    // should reach the explanation before the control that removes it.
    expect(link.compareDocumentPosition(button)).toBe(
      Node.DOCUMENT_POSITION_FOLLOWING,
    );
  });

  it("renders no action element when none is passed", () => {
    render(<DismissibleNotice onDismiss={vi.fn()}>Bare.</DismissibleNotice>);
    expect(screen.queryByRole("link")).not.toBeInTheDocument();
  });

  it("names the dismiss control for the notice it closes", () => {
    render(
      <DismissibleNotice onDismiss={vi.fn()} dismissLabel="Dismiss the savings notice">
        Named.
      </DismissibleNotice>,
    );
    expect(
      screen.getByRole("button", { name: "Dismiss the savings notice" }),
    ).toBeInTheDocument();
  });

  it("touches no browser storage", () => {
    const getItem = vi.spyOn(Storage.prototype, "getItem");
    const setItem = vi.spyOn(Storage.prototype, "setItem");

    render(<DismissibleNotice onDismiss={vi.fn()}>No storage.</DismissibleNotice>);
    fireEvent.click(screen.getByRole("button", { name: /dismiss/i }));

    // Persistence belongs to the host, keyed by repository and accounting
    // version. A primitive that remembered dismissals itself could only ever
    // key them one way.
    expect(getItem).not.toHaveBeenCalled();
    expect(setItem).not.toHaveBeenCalled();
    getItem.mockRestore();
    setItem.mockRestore();
  });
});
