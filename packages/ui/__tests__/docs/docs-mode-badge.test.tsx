import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { DocsModeBadge } from "../../src/docs/docs-mode-badge.js";

describe("DocsModeBadge", () => {
  it("renders nothing when there are no docs", () => {
    const { container } = render(<DocsModeBadge mode="none" />);
    expect(container).toBeEmptyDOMElement();
  });

  it("labels a template repo as Auto docs, never fully written", () => {
    render(<DocsModeBadge mode="deterministic" />);
    expect(screen.getByText("Auto docs")).toBeInTheDocument();
  });

  it("labels a partly-upgraded repo as Mixed docs", () => {
    render(<DocsModeBadge mode="mixed" />);
    expect(screen.getByText("Mixed docs")).toBeInTheDocument();
  });

  it("labels a fully model-written repo as AI docs", () => {
    render(<DocsModeBadge mode="llm" />);
    expect(screen.getByText("AI docs")).toBeInTheDocument();
  });
});
