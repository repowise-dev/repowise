import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { SourceCitation } from "../../src/chat/source-citation.js";

describe("SourceCitation", () => {
  it("renders a file label with line range when provided", () => {
    render(
      <SourceCitation
        citation={{
          file_path: "packages/ui/src/chat/artifacts.tsx",
          start_line: 10,
          end_line: 25,
        }}
        index={1}
      />,
    );
    expect(screen.getByText("artifacts.tsx")).toBeInTheDocument();
    expect(screen.getByText("L10-25")).toBeInTheDocument();
    expect(screen.getByText("1")).toBeInTheDocument();
  });

  it("appends symbol name when present", () => {
    render(
      <SourceCitation
        citation={{
          file_path: "src/foo.py",
          symbol_name: "do_thing",
        }}
      />,
    );
    expect(screen.getByText("foo.py::do_thing")).toBeInTheDocument();
  });

  it("becomes a link when buildHref is supplied", () => {
    render(
      <SourceCitation
        citation={{ file_path: "a/b.ts" }}
        buildHref={(c) => `/wiki/${encodeURIComponent(c.file_path)}`}
      />,
    );
    const link = screen.getByText("b.ts").closest("a");
    expect(link).not.toBeNull();
    expect(link?.getAttribute("href")).toBe("/wiki/a%2Fb.ts");
  });
});
