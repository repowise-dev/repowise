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
    const button = screen.getByRole("button", { name: "Copy prompt for your agent: src/a.ts" });
    expect(button).toHaveTextContent("Copy prompt for your agent");
    expect(button.className).toMatch(/opacity-0/);
    expect(button.className).toMatch(/group-hover\/row:opacity-100/);
    expect(button.className).toMatch(/group-focus-within\/row:opacity-100/);
    expect(button.className).toMatch(/focus-visible:opacity-100/);
    // No resting fill: the model tint appears on hover only.
    expect(button.className.split(" ")).not.toContain("bg-[var(--color-model-muted)]");
    fireEvent.click(button);
    expect(onClick).toHaveBeenCalledOnce();
    // The row's own click (open the file) does not fire as well.
    expect(onRow).not.toHaveBeenCalled();
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
