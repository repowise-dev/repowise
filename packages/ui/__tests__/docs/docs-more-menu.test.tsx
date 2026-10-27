import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { Sparkles } from "lucide-react";
import { DocsMoreMenu, type DocsMenuGroup } from "../../src/docs/docs-more-menu.js";

function groups(over: Partial<Record<string, () => void>> = {}): DocsMenuGroup[] {
  return [
    {
      key: "export",
      items: [
        { key: "all", label: "Export all pages (Markdown)", onSelect: over.all ?? vi.fn() },
        { key: "zip", label: "Download ZIP archive", href: "/export.zip", download: true },
      ],
    },
    { key: "present", items: [{ key: "present", label: "Present", onSelect: vi.fn() }] },
    {
      key: "model",
      items: [
        {
          key: "write-all",
          label: "Write all subsystem pages",
          icon: Sparkles,
          tone: "model",
          onSelect: over.writeAll ?? vi.fn(),
        },
      ],
    },
  ];
}

function open(gs: DocsMenuGroup[] = groups()) {
  render(<DocsMoreMenu groups={gs} />);
  fireEvent.click(screen.getByRole("button", { name: "More" }));
}

describe("DocsMoreMenu", () => {
  it("holds export, present and the bulk write behind one trigger", () => {
    open();
    expect(screen.getAllByRole("menuitem").map((i) => i.textContent)).toEqual([
      "Export all pages (Markdown)",
      "Download ZIP archive",
      "Present",
      "Write all subsystem pages",
    ]);
    expect(screen.getByRole("menuitem", { name: "Download ZIP archive" })).toHaveAttribute(
      "href",
      "/export.zip",
    );
  });

  it("marks the bulk write as model work", () => {
    open();
    expect(
      screen.getByRole("menuitem", { name: "Write all subsystem pages" }).className,
    ).toContain("--color-model");
  });

  it("runs the chosen item and closes", () => {
    const writeAll = vi.fn();
    open(groups({ writeAll }));
    fireEvent.click(screen.getByRole("menuitem", { name: "Write all subsystem pages" }));
    expect(writeAll).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole("menu")).toBeNull();
  });

  it("moves focus with the arrow keys, wrapping at the ends", () => {
    open();
    const menu = screen.getByRole("menu");
    const items = screen.getAllByRole("menuitem");
    items[0]!.focus();
    fireEvent.keyDown(menu, { key: "ArrowDown" });
    expect(document.activeElement).toBe(items[1]);
    fireEvent.keyDown(menu, { key: "End" });
    expect(document.activeElement).toBe(items[3]);
    fireEvent.keyDown(menu, { key: "ArrowDown" });
    expect(document.activeElement).toBe(items[0]);
    fireEvent.keyDown(menu, { key: "ArrowUp" });
    expect(document.activeElement).toBe(items[3]);
  });

  it("renders choices as radio items", () => {
    open([
      {
        key: "reader",
        label: "Reader level",
        phoneOnly: true,
        items: [
          { key: "a", label: "Overview", checked: false, onSelect: vi.fn() },
          { key: "b", label: "Deep", checked: true, onSelect: vi.fn() },
        ],
      },
    ]);
    expect(screen.getByRole("menuitemradio", { name: "Deep" })).toHaveAttribute(
      "aria-checked",
      "true",
    );
    expect(screen.getByRole("group", { name: "Reader level" }).className).toContain("sm:hidden");
  });

  it("marks the view on screen and makes the trigger read as active", () => {
    render(
      <DocsMoreMenu
        groups={[{ key: "views", items: [{ key: "f", label: "Doc freshness", href: "/coverage", current: true }] }]}
        LinkComponent={({ children, ...rest }) => <a data-router="" {...rest}>{children}</a>}
      />,
    );
    const trigger = screen.getByRole("button", { name: "More" });
    expect(trigger.className).toContain("font-medium");
    fireEvent.click(trigger);
    const item = screen.getByRole("menuitem", { name: "Doc freshness" });
    expect(item).toHaveAttribute("aria-current", "page");
    expect(item).toHaveAttribute("data-router");
  });

  it("renders nothing when every group is empty", () => {
    const { container } = render(
      <DocsMoreMenu groups={[{ key: "x", items: [] }]} />,
    );
    expect(container).toBeEmptyDOMElement();
  });
});
