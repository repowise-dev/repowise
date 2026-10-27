import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, fireEvent, cleanup, waitFor } from "@testing-library/react";
import { PresentOverlay } from "../../src/present/present-overlay.js";
import type { PresentModel } from "../../src/present/types.js";

const MODEL: PresentModel = {
  repoName: "acme",
  slides: [
    { id: "title", kind: "title", title: "acme", body: "Acme ships orders." },
    { id: "part", kind: "part", eyebrow: "Part 1 of 1", title: "Order Intake", sourcePageId: "m1" },
    { id: "start", kind: "start", title: "Where to start reading", start: [] },
  ],
};

function open(onClose = vi.fn()) {
  render(<PresentOverlay model={MODEL} onClose={onClose} onOpenPage={() => {}} />);
  return onClose;
}

const heading = () => screen.getByRole("heading", { level: 2 }).textContent;

// The outgoing slide fades before the next mounts, so wait for the swap.
const showing = (title: string) => waitFor(() => expect(heading()).toBe(title));

afterEach(() => {
  cleanup();
  document.body.innerHTML = "";
});

describe("PresentOverlay keyboard", () => {
  it("moves with the arrow keys and closes on Escape", async () => {
    const onClose = open();
    expect(heading()).toBe("acme");
    fireEvent.keyDown(window, { key: "ArrowRight" });
    await showing("Order Intake");
    fireEvent.keyDown(window, { key: "End" });
    await showing("Where to start reading");
    fireEvent.keyDown(window, { key: "ArrowLeft" });
    await showing("Order Intake");
    fireEvent.keyDown(window, { key: "Escape" });
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("announces the slide title with its position", async () => {
    open();
    fireEvent.keyDown(window, { key: "ArrowRight" });
    expect(await screen.findByText("Slide 2 of 3: Order Intake")).toBeInTheDocument();
  });

  it("leaves every key to a modal opened from a slide", async () => {
    const onClose = open();
    const nested = document.createElement("div");
    nested.setAttribute("role", "dialog");
    nested.setAttribute("aria-modal", "true");
    document.body.appendChild(nested);

    fireEvent.keyDown(nested, { key: "ArrowRight" });
    fireEvent.keyDown(nested, { key: "Escape" });
    // The position updates synchronously, unlike the animated slide.
    expect(screen.getByText("Slide 1 of 3: acme")).toBeInTheDocument();
    expect(onClose).not.toHaveBeenCalled();

    nested.remove();
    fireEvent.keyDown(window, { key: "ArrowRight" });
    await showing("Order Intake");
  });

  it("lets Space press a focused button instead of advancing", () => {
    open();
    const close = screen.getByRole("button", { name: /close presentation/i });
    close.focus();
    fireEvent.keyDown(close, { key: " " });
    expect(screen.getByText("Slide 1 of 3: acme")).toBeInTheDocument();
  });

  it("keeps Tab inside the dialog and returns focus to the opener on close", () => {
    const opener = document.createElement("button");
    document.body.appendChild(opener);
    opener.focus();

    const { unmount } = render(<PresentOverlay model={MODEL} onClose={() => {}} />);
    const dialog = screen.getByRole("dialog");
    expect(document.activeElement).toBe(dialog);

    fireEvent.keyDown(window, { key: "Tab" });
    expect(dialog.contains(document.activeElement)).toBe(true);
    fireEvent.keyDown(window, { key: "Tab", shiftKey: true });
    expect(dialog.contains(document.activeElement)).toBe(true);

    unmount();
    expect(document.activeElement).toBe(opener);
  });
});
