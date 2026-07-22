import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { AutoDocsBanner } from "../../src/docs/auto-docs-banner.js";

describe("AutoDocsBanner", () => {
  it("renders nothing when no template pages remain", () => {
    const { container } = render(
      <AutoDocsBanner templateCount={0} writtenCount={12} onWriteAll={vi.fn()} />,
    );
    expect(container).toBeEmptyDOMElement();
  });

  it("shows the count (formatted) and offers the bulk write", () => {
    const onWriteAll = vi.fn();
    render(
      <AutoDocsBanner templateCount={1238} writtenCount={0} onWriteAll={onWriteAll} />,
    );
    expect(screen.getByText("1,238")).toBeInTheDocument();
    expect(screen.getByText(/auto-documented from your code/i)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /Write with AI/ }));
    expect(onWriteAll).toHaveBeenCalledTimes(1);
  });

  it("words a mixed repo honestly (pages still generated from structure)", () => {
    render(
      <AutoDocsBanner templateCount={5} writtenCount={40} onWriteAll={vi.fn()} />,
    );
    expect(screen.getByText(/still/i)).toBeInTheDocument();
  });

  it("shows a dismiss control only when the host owns dismissal", () => {
    const onDismiss = vi.fn();
    const { rerender } = render(
      <AutoDocsBanner templateCount={3} writtenCount={0} onWriteAll={vi.fn()} />,
    );
    expect(screen.queryByRole("button", { name: /Dismiss/ })).not.toBeInTheDocument();
    rerender(
      <AutoDocsBanner
        templateCount={3}
        writtenCount={0}
        onWriteAll={vi.fn()}
        onDismiss={onDismiss}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /Dismiss/ }));
    expect(onDismiss).toHaveBeenCalledTimes(1);
  });
});
