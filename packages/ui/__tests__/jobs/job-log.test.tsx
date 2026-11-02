import { describe, it, expect, vi, beforeAll } from "vitest";
import { render, screen } from "@testing-library/react";
import { JobLog } from "../../src/jobs/job-log.js";

describe("JobLog", () => {
  beforeAll(() => {
    // jsdom has no layout, so no scrollIntoView; the log calls it on update.
    Object.defineProperty(Element.prototype, "scrollIntoView", {
      configurable: true,
      value: vi.fn(),
    });
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
    const viewport = container.querySelector("[data-radix-scroll-area-viewport]");
    expect(viewport).toHaveClass("[&>div]:block!");
  });
});
