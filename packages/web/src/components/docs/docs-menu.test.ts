import { describe, expect, it, vi } from "vitest";
import { buildDocsMenuGroups, type DocsMenuInput } from "./docs-menu";

const t = (key: string) => key;

function input(over: Partial<DocsMenuInput> = {}): DocsMenuInput {
  return {
    t,
    hasPage: true,
    onExportPage: vi.fn(),
    onExportAll: vi.fn(),
    exporting: false,
    zipHref: "/api/repos/r1/export",
    onPresent: vi.fn(),
    reader: null,
    ...over,
  };
}

const keys = (over: Partial<DocsMenuInput> = {}) =>
  buildDocsMenuGroups(input(over)).flatMap((g) => g.items.map((i) => i.key));

describe("buildDocsMenuGroups", () => {
  it("holds export and present, and leaves the bulk write to the bar", () => {
    expect(keys()).toEqual(["export-page", "export-all", "export-zip", "present"]);
  });

  it("holds the exports while one is running", () => {
    const items = buildDocsMenuGroups(input({ exporting: true })).flatMap((g) => g.items);
    expect(items.find((i) => i.key === "export-all")?.disabled).toBe(true);
    expect(items.find((i) => i.key === "export-page")?.disabled).toBe(true);
  });

  it("offers present only when there is a deck", () => {
    expect(keys({ onPresent: null })).not.toContain("present");
  });

  it("leaves out the page export without a page", () => {
    expect(keys({ hasPage: false })).not.toContain("export-page");
  });

  it("carries the reader level as a phone-only last group", () => {
    const setPersona = vi.fn();
    const groups = buildDocsMenuGroups(
      input({ reader: { persona: "deep", setPersona } }),
    );
    const reader = groups[groups.length - 1]!;
    expect(reader.phoneOnly).toBe(true);
    expect(reader.items.find((i) => i.checked)?.key).toBe("reader-deep");
    reader.items[0]!.onSelect?.();
    expect(setPersona).toHaveBeenCalledWith("overview");
  });
});
