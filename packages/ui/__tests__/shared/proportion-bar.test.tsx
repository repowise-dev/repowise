import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent, within } from "@testing-library/react";

import { ProportionBar, shareColor, SHARE_TAIL } from "../../src/shared/proportion-bar";

const seg = (key: string, value: number) => ({ key, label: key, value });

function legendItems(label: string) {
  return within(screen.getByRole("list", { name: label })).getAllByRole("listitem");
}

function fills(container: HTMLElement) {
  const bar = container.querySelector("[aria-hidden='true'], [role='img']")!;
  return Array.from(bar.children).map((el) => (el as HTMLElement).getAttribute("style") ?? "");
}

describe("ProportionBar", () => {
  it("orders largest first and steps down the share palette", () => {
    const { container } = render(
      <ProportionBar label="Split" segments={[seg("b", 10), seg("a", 60), seg("c", 30)]} />,
    );
    expect(legendItems("Split").map((li) => li.textContent)).toEqual(["a60%", "c30%", "b10%"]);
    const styles = fills(container);
    expect(styles[0]).toContain("--color-share-1");
    expect(styles[1]).toContain("--color-share-2");
    expect(styles[2]).toContain("--color-share-3");
  });

  it("folds the long tail into one named tail segment", () => {
    render(
      <ProportionBar
        label="Split"
        maxSegments={2}
        segments={[seg("a", 50), seg("b", 30), seg("c", 15), seg("d", 5)]}
      />,
    );
    const items = legendItems("Split").map((li) => li.textContent);
    expect(items).toEqual(["a50%", "b30%", "2 others20%"]);
  });

  it("keeps the given order and semantic colours when sorting is off", () => {
    const { container } = render(
      <ProportionBar
        label="Bands"
        sort={false}
        segments={[
          { key: "good", label: "Good", value: 10, color: "var(--color-success)" },
          { key: "bad", label: "Bad", value: 90, color: "var(--color-error)" },
        ]}
      />,
    );
    expect(legendItems("Bands").map((li) => li.textContent)).toEqual(["Good10%", "Bad90%"]);
    const styles = fills(container);
    expect(styles[0]).toContain("--color-success");
    expect(styles[1]).toContain("--color-error");
  });

  it("drops empty segments and renders nothing for an empty whole", () => {
    const { container, rerender } = render(
      <ProportionBar label="Split" segments={[seg("a", 3), seg("b", 0)]} />,
    );
    expect(legendItems("Split")).toHaveLength(1);
    rerender(<ProportionBar label="Split" segments={[seg("a", 0)]} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("summarises the split on the bar when the legend is off", () => {
    render(
      <ProportionBar
        label="Findings"
        legend={false}
        segments={[seg("high", 1), { ...seg("low", 299), detail: "299" }]}
      />,
    );
    expect(screen.getByRole("img", { name: "Findings: low 299, high <1%" })).toBeInTheDocument();
    expect(screen.queryByRole("list")).not.toBeInTheDocument();
  });

  it("links or selects from the legend", () => {
    const onSelect = vi.fn();
    render(
      <ProportionBar
        label="Split"
        segments={[
          { ...seg("a", 2), href: "/a" },
          { ...seg("b", 1), onSelect },
        ]}
      />,
    );
    expect(screen.getByRole("link", { name: /a/ })).toHaveAttribute("href", "/a");
    fireEvent.click(screen.getByRole("button", { name: /b/ }));
    expect(onSelect).toHaveBeenCalledOnce();
  });

  it("puts an explicit tail last in the tail step", () => {
    render(
      <ProportionBar
        label="Split"
        segments={[{ key: "rest", label: "Others", value: 80, tail: true }, seg("a", 20)]}
      />,
    );
    expect(legendItems("Split").map((li) => li.textContent)).toEqual(["a20%", "Others80%"]);
    expect(shareColor(5)).toBe(SHARE_TAIL);
  });
});
