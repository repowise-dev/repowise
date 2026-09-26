import { afterEach, describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { ViewTabs } from "../../src/shared/view-tabs.js";

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

  describe("keeping the active tab in view", () => {
    // jsdom has no layout, so give the row and its tabs a geometry: tabs 100px
    // wide side by side in a 150px row.
    const restore: (() => void)[] = [];
    function stub<K extends keyof HTMLElement>(key: K, get: (el: HTMLElement) => number) {
      const original = Object.getOwnPropertyDescriptor(HTMLElement.prototype, key);
      Object.defineProperty(HTMLElement.prototype, key, {
        configurable: true,
        get(this: HTMLElement) {
          return get(this);
        },
      });
      restore.push(() => {
        if (original) Object.defineProperty(HTMLElement.prototype, key, original);
      });
    }
    afterEach(() => {
      while (restore.length) restore.pop()!();
    });

    it("scrolls the tab row, not the page", () => {
      const scrolled = new WeakMap<Element, number>();
      const original = Object.getOwnPropertyDescriptor(Element.prototype, "scrollLeft");
      Object.defineProperty(Element.prototype, "scrollLeft", {
        configurable: true,
        get(this: Element) {
          return scrolled.get(this) ?? 0;
        },
        set(this: Element, v: number) {
          scrolled.set(this, v);
        },
      });
      restore.push(() => {
        if (original) Object.defineProperty(Element.prototype, "scrollLeft", original);
      });
      stub("offsetWidth", (el) => (el.getAttribute("role") === "tab" ? 100 : 0));
      stub("offsetLeft", (el) =>
        el.getAttribute("role") === "tab" ? [...el.parentElement!.children].indexOf(el) * 100 : 0,
      );
      stub("clientWidth", (el) => (el.getAttribute("role") === "tablist" ? 150 : 0));
      const scrollTo = vi.spyOn(window, "scrollTo").mockImplementation(() => {});

      render(
        <ViewTabs
          tabs={[
            { id: "a", label: "A" },
            { id: "b", label: "B" },
            { id: "c", label: "C" },
          ]}
          value="c"
          onValueChange={() => {}}
        />,
      );

      // Tab C spans 200 to 300, so the 150px row scrolls to 150 to show it.
      expect(screen.getByRole("tablist").scrollLeft).toBe(150);
      expect(scrollTo).not.toHaveBeenCalled();
      expect(window.scrollY).toBe(0);
      scrollTo.mockRestore();
    });
  });
});
