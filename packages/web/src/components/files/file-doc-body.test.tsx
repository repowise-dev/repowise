// @vitest-environment jsdom

import React from "react";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { FileDocBody } from "./file-doc-body";

const mocks = vi.hoisted(() => ({
  pages: [] as { id: string; target_path: string }[],
}));

vi.mock("@/lib/hooks/use-pages", () => ({
  usePages: () => ({ pages: mocks.pages, error: undefined, isLoading: false, mutate: vi.fn() }),
}));
vi.mock("next/link", () => ({
  default: ({ href, children, ...rest }: { href: string; children: React.ReactNode }) => (
    <a href={href} {...rest}>
      {children}
    </a>
  ),
}));

describe("FileDocBody", () => {
  // No `globals`, so nothing unmounts the previous render for us.
  afterEach(cleanup);

  it("links a backticked path in the embedded body to that file's route", () => {
    mocks.pages = [
      { id: "file_page:packages/core/src/parser.py", target_path: "packages/core/src/parser.py" },
    ];

    render(
      <FileDocBody
        repoId="r1"
        content="The walker calls `packages/core/src/parser.py` once per file."
      />,
    );

    const link = screen.getByRole("link", { name: /parser\.py/ });
    expect(link.getAttribute("href")).toBe("/repos/r1/files/packages/core/src/parser.py");
  });

  it("leaves a path with no page behind it as plain text", () => {
    mocks.pages = [];

    render(<FileDocBody repoId="r1" content="See `packages/core/src/parser.py`." />);

    expect(screen.queryByRole("link")).toBeNull();
  });

  it("sends a directory ref to the docs reader, since the file route 404s on one", () => {
    // The path index resolves a directory to its module page, and a module
    // page is not a file: linking it to the file route is a dead link.
    mocks.pages = [{ id: "module_page:packages/ui", target_path: "packages/ui" }];

    render(<FileDocBody repoId="r1" content="Lives under `packages/ui` today." />);

    const link = screen.getByRole("link", { name: /packages\/ui/ });
    expect(link.getAttribute("href")).toBe("/repos/r1/docs?page=module_page%3Apackages%2Fui");
  });
});
