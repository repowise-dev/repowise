import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen } from "@testing-library/react";
import { JobLog } from "../../src/jobs/job-log.js";

const VIEWPORT = "[data-radix-scroll-area-viewport]";

describe("JobLog", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("shows only the last maxLines entries", () => {
    render(<JobLog entries={["a", "b", "c"].map((text) => ({ text }))} maxLines={2} />);
    expect(screen.queryByText("a")).not.toBeInTheDocument();
    expect(screen.getByText("b")).toBeInTheDocument();
    expect(screen.getByText("c")).toBeInTheDocument();
  });

  it("overrides the viewport's table wrapper so long lines truncate", () => {
    // Radix's `display:table` wrapper grows to the widest line; left alone, a
    // long line widened the panel instead of truncating (#4690).
    const { container } = render(<JobLog entries={[{ text: "x".repeat(500) }]} />);
    const viewport = container.querySelector(VIEWPORT);
    expect(viewport).toHaveClass("[&>div]:block!");
  });

  it("follows new lines inside its own viewport without scrolling the page", () => {
    // `scrollIntoView` scrolls every scrollable ancestor, so it moved the whole
    // page on each new line (#5469). jsdom has no layout, so give the viewport
    // a content height to scroll to.
    const scrollIntoView = vi.fn();
    Object.defineProperty(Element.prototype, "scrollIntoView", {
      configurable: true,
      value: scrollIntoView,
    });
    vi.spyOn(HTMLElement.prototype, "scrollHeight", "get").mockReturnValue(400);

    const { container, rerender } = render(<JobLog entries={[{ text: "a" }]} />);
    const viewport = container.querySelector(VIEWPORT) as HTMLElement;
    expect(viewport.scrollTop).toBe(400);

    viewport.scrollTop = 0;
    rerender(<JobLog entries={[{ text: "a" }, { text: "b" }]} />);
    expect(viewport.scrollTop).toBe(400);
    expect(scrollIntoView).not.toHaveBeenCalled();
  });
});
