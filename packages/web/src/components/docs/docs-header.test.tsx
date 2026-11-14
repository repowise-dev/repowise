// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const nav = vi.hoisted(() => ({ pathname: "/repos/r1/docs" }));

vi.mock("next/navigation", () => ({
  usePathname: () => nav.pathname,
  useParams: () => ({ id: "r1" }),
}));
vi.mock("swr", () => ({ default: () => ({ data: undefined }) }));
vi.mock("@/lib/api/doc-drift", () => ({ getDocDrift: vi.fn() }));

import { DocsHeader } from "./docs-header";

function openMore() {
  fireEvent.click(screen.getByRole("button", { name: "More" }));
}

describe("DocsHeader", () => {
  afterEach(cleanup);
  beforeEach(() => {
    nav.pathname = "/repos/r1/docs";
  });

  it("switches between Explorer and Doc drift only", () => {
    render(<DocsHeader />);
    const views = screen.getByRole("navigation", { name: "Docs views" });
    expect(Array.from(views.querySelectorAll("a")).map((a) => a.getAttribute("href"))).toEqual([
      "/repos/r1/docs",
      "/repos/r1/docs/drift",
    ]);
  });

  it("links Doc freshness from the More menu", () => {
    render(<DocsHeader />);
    openMore();
    const item = screen.getByRole("menuitem", { name: "Doc freshness" });
    expect(item.getAttribute("href")).toBe("/repos/r1/docs/coverage");
    expect(item.getAttribute("aria-current")).toBeNull();
  });

  it("marks Doc freshness as the current view on its route", () => {
    nav.pathname = "/repos/r1/docs/coverage";
    render(<DocsHeader />);
    const views = screen.getByRole("navigation", { name: "Docs views" });
    expect(views.querySelector('[aria-current="page"]')).toBeNull();
    openMore();
    expect(
      screen.getByRole("menuitem", { name: "Doc freshness" }).getAttribute("aria-current"),
    ).toBe("page");
  });

  it("appends the page's own items after the view link", () => {
    render(
      <DocsHeader
        menuGroups={[{ key: "export", items: [{ key: "zip", label: "Download ZIP archive", href: "/x.zip", download: true }] }]}
      />,
    );
    openMore();
    expect(screen.getAllByRole("menuitem").map((i) => i.textContent)).toEqual([
      "Doc freshness",
      "Download ZIP archive",
    ]);
  });
});
