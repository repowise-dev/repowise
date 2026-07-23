import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
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
});
