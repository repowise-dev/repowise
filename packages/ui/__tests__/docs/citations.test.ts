import { describe, it, expect } from "vitest";
import { citationKey, citationLabel, readCitations } from "../../src/docs/citations.js";

describe("readCitations", () => {
  it("reads spans, symbols and forge links, and keeps old rows readable", () => {
    const rows = readCitations(
      [
        { path: "a.py", kind: "step", start_line: 10, end_line: 40, symbol: "run", url: "https://x/a.py#L10-L40" },
        { path: "b.py", kind: "dependency" },
        { path: "", kind: "dependency" },
        { path: "c.py", kind: "cited", start_line: 7, end_line: 7 },
      ],
      (p) => (p === "b.py" ? "file_page:b.py" : undefined),
    );
    expect(rows.map(citationLabel)).toEqual(["a.py:10-40", "b.py", "c.py:7"]);
    expect(rows[0]).toMatchObject({ symbol: "run", url: "https://x/a.py#L10-L40", startLine: 10 });
    expect(rows[1]).toMatchObject({ pageId: "file_page:b.py", lines: "" });
  });

  it("keys two spans in one file apart", () => {
    const rows = readCitations(
      [
        { path: "a.py", kind: "step", start_line: 1, end_line: 5 },
        { path: "a.py", kind: "step", start_line: 9, end_line: 12 },
      ],
      () => undefined,
    );
    expect(new Set(rows.map(citationKey)).size).toBe(2);
  });

  it("is empty for anything that is not a list", () => {
    expect(readCitations(undefined, () => undefined)).toEqual([]);
    expect(readCitations({ path: "a.py" }, () => undefined)).toEqual([]);
  });
});
