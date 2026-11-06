import { describe, it, expect, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { DocsModeBadge } from "../../src/docs/docs-mode-badge.js";

describe("DocsModeBadge", () => {
  it("renders nothing when there are no docs", () => {
    const { container } = render(<DocsModeBadge mode="none" />);
    expect(container).toBeEmptyDOMElement();
  });

  it("flags a repo whose subsystem pages are still stubs", () => {
    render(<DocsModeBadge mode="deterministic" />);
    expect(screen.getByText("Subsystem pages unwritten")).toBeInTheDocument();
  });

  it("renders nothing on a fully written wiki", () => {
    const { container } = render(<DocsModeBadge mode="llm" />);
    expect(container).toBeEmptyDOMElement();
  });

  it("becomes the action that offers a model", () => {
    const onAction = vi.fn();
    render(<DocsModeBadge mode="deterministic" onAction={onAction} />);
    fireEvent.click(screen.getByRole("button", { name: /Write with a model/ }));
    expect(onAction).toHaveBeenCalledOnce();
  });

  it("offers no action once the wiki is written", () => {
    const { container } = render(<DocsModeBadge mode="llm" onAction={vi.fn()} />);
    expect(container).toBeEmptyDOMElement();
  });
});
