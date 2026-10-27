import { describe, it, expect, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";

import { PromptForRowsButton, RowPromptAction } from "../../src/health/row-prompt-action";

describe("RowPromptAction", () => {
  it("is a ghost action, hidden until row hover, focus or selection, and named for its row", () => {
    const onClick = vi.fn();
    const onRow = vi.fn();
    render(
      <div className="group/row" onClick={onRow}>
        <RowPromptAction subject="src/a.ts" onClick={onClick} />
      </div>,
    );
    const button = screen.getByRole("button", { name: "Copy prompt: src/a.ts" });
    expect(button).toHaveTextContent("Copy prompt");
    expect(button.className).toMatch(/opacity-0/);
    expect(button.className).toMatch(/group-hover\/row:opacity-100/);
    expect(button.className).toMatch(/group-focus-within\/row:opacity-100/);
    expect(button.className).toMatch(/focus-visible:opacity-100/);
    expect(button.className).toMatch(/\[@media\(hover:none\)\]:opacity-100/);
    // Neutral ink: plum is kept for model work.
    expect(button.className).not.toMatch(/color-model/);
    // Revealed, it reads as an action: ink label, soft fill on hover.
    expect(button.className).toContain("text-[var(--color-text-primary)]");
    expect(button.className).toContain("hover:bg-[var(--color-action-soft-hover)]");
    fireEvent.click(button);
    expect(onClick).toHaveBeenCalledOnce();
    // The row's own click (open the file) does not fire as well.
    expect(onRow).not.toHaveBeenCalled();
  });
});

describe("RowPromptAction icon variant", () => {
  it("drops the visible label and keeps the row's name as the accessible name", () => {
    render(
      <div className="group/row">
        <RowPromptAction variant="icon" label="Fix a.ts:3" onClick={vi.fn()} />
      </div>,
    );
    const button = screen.getByRole("button", { name: "Fix a.ts:3" });
    expect(button).toHaveTextContent("");
    expect(button.className).toMatch(/opacity-0/);
  });
});

describe("PromptForRowsButton", () => {
  it("counts the rows it covers and renders nothing for none", () => {
    const onClick = vi.fn();
    const { rerender, container } = render(<PromptForRowsButton count={12} onClick={onClick} />);
    fireEvent.click(screen.getByRole("button", { name: "Copy prompt for these 12" }));
    expect(onClick).toHaveBeenCalledOnce();
    rerender(<PromptForRowsButton count={0} onClick={onClick} />);
    expect(container).toBeEmptyDOMElement();
  });
});
