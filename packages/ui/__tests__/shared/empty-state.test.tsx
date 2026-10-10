import { describe, it, expect, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { Inbox } from "lucide-react";

import { AllClearStat, EmptyState } from "../../src/shared/empty-state";

describe("EmptyState", () => {
  it("defaults to a calm neutral box with no warm wash or gradient tile", () => {
    const { container } = render(
      <EmptyState
        icon={<Inbox className="h-8 w-8" />}
        title="No commits yet"
        description="Index to populate."
      />,
    );
    const root = container.firstElementChild as HTMLElement;
    expect(root).toHaveAttribute("data-tone", "neutral");
    expect(root.className).not.toMatch(/gradient-warm|border-dashed|shadow/);
    expect(root.className).toMatch(/border-\[var\(--color-border-default\)\]/);
    expect(screen.getByRole("heading", { name: "No commits yet" })).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("keeps the onClick action shape existing callers pass", () => {
    const onClick = vi.fn();
    render(<EmptyState title="Empty" action={{ label: "Add", onClick }} />);
    fireEvent.click(screen.getByRole("button", { name: "Add" }));
    expect(onClick).toHaveBeenCalledOnce();
  });

  it("renders an href action as a link, plus a secondary action", () => {
    const onClick = vi.fn();
    render(
      <EmptyState
        title="Empty"
        action={{ label: "Open docs", href: "/docs" }}
        secondaryAction={{ label: "Later", onClick }}
      />,
    );
    expect(screen.getByRole("link", { name: "Open docs" })).toHaveAttribute("href", "/docs");
    fireEvent.click(screen.getByRole("button", { name: "Later" }));
    expect(onClick).toHaveBeenCalledOnce();
  });

  it("positive draws the success check and no box", () => {
    const { container } = render(
      <EmptyState tone="positive" title="No dead code across 1,240 files" />,
    );
    const root = container.firstElementChild as HTMLElement;
    expect(root.className).not.toMatch(/\bborder\b/);
    expect(container.querySelector("[class*='color-success-muted']")).not.toBeNull();
  });

  it("filtered is compact by default and hides the icon", () => {
    const onClear = vi.fn();
    const { container } = render(
      <EmptyState
        tone="filtered"
        icon={<Inbox data-testid="icon" />}
        title="12 hidden by these filters"
        action={{ label: "Clear filters", onClick: onClear }}
      />,
    );
    expect(container.firstElementChild).toHaveClass("flex-wrap");
    expect(screen.queryByTestId("icon")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Clear filters" }));
    expect(onClear).toHaveBeenCalledOnce();
  });

  it("error announces as an alert", () => {
    render(<EmptyState tone="error" title="Could not load" description="Try again." />);
    expect(screen.getByRole("alert")).toHaveTextContent("Could not load");
  });

  it("bare drops the hairline box", () => {
    const { container } = render(<EmptyState bare title="Nothing" />);
    expect((container.firstElementChild as HTMLElement).className).not.toMatch(/\bborder\b/);
  });

  it("respects titleAs", () => {
    render(<EmptyState title="Panel" titleAs="h2" />);
    expect(screen.getByRole("heading", { level: 2, name: "Panel" })).toBeInTheDocument();
  });
});

describe("AllClearStat", () => {
  it("renders a zero with the all-clear word", () => {
    render(<AllClearStat />);
    expect(screen.getByText("0")).toBeInTheDocument();
    expect(screen.getByText("All clear")).toBeInTheDocument();
  });
});
