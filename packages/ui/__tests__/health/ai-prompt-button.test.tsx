import { afterEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, render, screen } from "@testing-library/react";

import { AiPromptButton, promptPeek } from "../../src/health/ai-prompt-button";

afterEach(() => {
  vi.useRealTimers();
});

describe("AiPromptButton", () => {
  it("is a soft ink button with a copy icon, never plum", () => {
    const onClick = vi.fn();
    render(<AiPromptButton onClick={onClick} />);
    const button = screen.getByRole("button", { name: "Agent prompt" });
    expect(button.className).not.toMatch(/color-model/);
    expect(button.className).toContain("border-[var(--color-border-default)]");
    expect(button.className).toContain("bg-[var(--color-action-soft)]");
    expect(button.className).toContain("hover:bg-[var(--color-action-soft-hover)]");
    expect(button.className).toContain("text-[var(--color-text-primary)]");
    fireEvent.click(button);
    expect(onClick).toHaveBeenCalledOnce();
  });

  it("copies the prompt, says Copied for a moment, then reverts", async () => {
    vi.useFakeTimers();
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.assign(navigator, { clipboard: { writeText } });
    render(<AiPromptButton label="Copy prompt" ariaLabel="Copy prompt: Index" copyText={() => "line one\nline two"} />);

    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Copy prompt: Index" }));
    });
    expect(writeText).toHaveBeenCalledWith("line one\nline two");
    expect(screen.getByRole("button", { name: "Copy prompt: Index" })).toHaveTextContent("Copied");
    expect(screen.getByText("Copied", { selector: "[aria-live='polite']" })).toBeTruthy();

    act(() => {
      vi.advanceTimersByTime(1600);
    });
    expect(screen.getByRole("button", { name: "Copy prompt: Index" })).toHaveTextContent("Copy prompt");
  });

  it("says the copy failed, never Copied, when the clipboard is blocked", async () => {
    Object.assign(navigator, { clipboard: { writeText: vi.fn().mockRejectedValue(new Error("denied")) } });
    render(<AiPromptButton ariaLabel="Copy prompt: Index" copyText={() => "x"} />);
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Copy prompt: Index" }));
    });
    expect(screen.queryByText("Copied")).toBeNull();
    expect(screen.getByRole("button", { name: "Copy prompt: Index" })).toHaveTextContent("Couldn't copy");
    expect(screen.getByText("Couldn't copy", { selector: "[aria-live='polite']" })).toBeTruthy();
  });
});

describe("promptPeek", () => {
  it("keeps the first non-empty lines and marks the rest", () => {
    expect(promptPeek("a\n\nb\nc\nd\ne")).toBe("a\nb\nc\nd\n...");
    expect(promptPeek("a\nb")).toBe("a\nb");
  });
});
