import { render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { HistoryFilters, type HistoryFilterValue } from "../../src/commits/history-filters";

const value: HistoryFilterValue = { impact: "all", hotspots: false, window: "month" };

describe("HistoryFilters", () => {
  it("disables the impact options with the reason while the window is catching up", () => {
    render(<HistoryFilters value={value} onChange={vi.fn()} impactPending />);

    const worse = screen.getByRole("radio", { name: /Made things worse/ });
    expect(worse.getAttribute("aria-disabled")).toBe("true");
    expect(worse.getAttribute("title")).toBe("Counts appear as commits are analysed by the current analyzer");
    const health = screen.getByRole("radiogroup", { name: "Health impact" });
    expect(within(health).getByRole("radio", { name: /^All/ }).getAttribute("aria-disabled")).toBeNull();
  });

  it("leaves the impact options live otherwise", () => {
    render(<HistoryFilters value={value} onChange={vi.fn()} />);

    expect(screen.getByRole("radio", { name: /Made things better/ }).getAttribute("aria-disabled")).toBeNull();
  });
});
