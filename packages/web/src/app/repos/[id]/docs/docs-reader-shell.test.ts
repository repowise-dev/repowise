import { describe, expect, it } from "vitest";
import { DOCS_READER_SHELL_CLASS } from "./docs-reader-shell";

describe("DOCS_READER_SHELL_CLASS", () => {
  it("carries a definite height, which DocsExplorer's h-full resolves against", () => {
    expect(DOCS_READER_SHELL_CLASS).toContain("h-full");
  });

  it("does not ask for the viewport, which overflows past the banners", () => {
    expect(DOCS_READER_SHELL_CLASS).not.toContain("h-screen");
  });

  it("does not lean on flex-1, which is inert under a parent that is not a flex container", () => {
    expect(DOCS_READER_SHELL_CLASS).not.toContain("flex-1");
  });

  it("is a flex column, so the tree and the reader sit side by side inside it", () => {
    expect(DOCS_READER_SHELL_CLASS).toContain("flex");
    expect(DOCS_READER_SHELL_CLASS).toContain("flex-col");
  });
});
