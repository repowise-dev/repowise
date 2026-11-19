/**
 * The docs header scrolls its actions horizontally, so a menu positioned inside
 * it is clipped and its items can't be clicked (#3678). The Export menu is
 * portalled out of the header instead.
 */
import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { ExportMenu } from "../../src/docs/docs-page-actions.js";

function renderInScrollingHeader(
  props: Partial<Parameters<typeof ExportMenu>[0]> = {},
) {
  const handlers = { onExportPage: vi.fn(), onExportAll: vi.fn() };
  render(
    <div
      data-testid="header-actions"
      className="ml-auto flex min-w-0 items-center gap-2 overflow-x-auto"
    >
      <ExportMenu
        isExporting={false}
        zipHref="/export.zip"
        hasPage
        {...handlers}
        {...props}
      />
    </div>,
  );
  fireEvent.click(screen.getByRole("button", { name: /export/i }));
  return handlers;
}

describe("ExportMenu", () => {
  it("renders its items outside the scrolling header", () => {
    renderInScrollingHeader();

    const items = screen.getAllByRole("menuitem");
    expect(items.map((item) => item.textContent)).toEqual([
      "This page (Markdown)",
      "All pages (Markdown)",
      "ZIP archive",
    ]);
    const header = screen.getByTestId("header-actions");
    for (const item of items) expect(header.contains(item)).toBe(false);
  });

  it("runs the chosen export and closes", () => {
    const { onExportAll } = renderInScrollingHeader();

    fireEvent.click(
      screen.getByRole("menuitem", { name: "All pages (Markdown)" }),
    );

    expect(onExportAll).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole("menuitem")).toBeNull();
  });

  it("leaves out the page export when there is no page", () => {
    renderInScrollingHeader({ hasPage: false });

    expect(
      screen.queryByRole("menuitem", { name: "This page (Markdown)" }),
    ).toBeNull();
    expect(screen.getAllByRole("menuitem")).toHaveLength(2);
  });
});
