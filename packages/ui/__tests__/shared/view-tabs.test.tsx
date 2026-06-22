import { describe, it, expect, vi, beforeAll } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { ViewTabs } from "../../src/shared/view-tabs.js";

// jsdom has no layout engine, so the active-tab scroll-into-view is a no-op here.
beforeAll(() => {
  Element.prototype.scrollIntoView = vi.fn();
});

describe("ViewTabs", () => {
  it("renders tab labels and reports selection", () => {
    const onValueChange = vi.fn();
    render(
      <ViewTabs
        tabs={[
          { id: "map", label: "Communities" },
          { id: "explore", label: "Explore" },
        ]}
        value="map"
        onValueChange={onValueChange}
      />,
    );

    expect(screen.getByRole("tab", { name: "Communities" }).getAttribute("aria-selected")).toBe(
      "true",
    );
    fireEvent.click(screen.getByRole("tab", { name: "Explore" }));
    expect(onValueChange).toHaveBeenCalledWith("explore");
  });

  it("renders an optional leading icon and stays label-only without one", () => {
    render(
      <ViewTabs
        tabs={[
          {
            id: "map",
            label: "Communities",
            icon: <svg data-testid="kg-icon" aria-hidden />,
          },
          { id: "explore", label: "Explore" },
        ]}
        value="map"
        onValueChange={() => {}}
      />,
    );

    // The canonical icon rides along with the tab definition…
    const iconTab = screen.getByRole("tab", { name: "Communities" });
    expect(iconTab.querySelector('[data-testid="kg-icon"]')).not.toBeNull();
    // …and a tab without one renders no icon wrapper.
    const plainTab = screen.getByRole("tab", { name: "Explore" });
    expect(plainTab.querySelector("svg")).toBeNull();
  });
});
