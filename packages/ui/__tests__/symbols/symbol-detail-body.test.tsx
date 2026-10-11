import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { SymbolDetailBody } from "../../src/symbols/symbol-detail-body.js";

describe("SymbolDetailBody", () => {
  it("labels the blame count as commits in current code and says what it counts", () => {
    render(
      <SymbolDetailBody
        data={{
          identity: { name: "run", kind: "function", file_path: "src/app.py", start_line: 3 },
          blame_mod_count: 4,
          blame_recent_mod_count: 1,
        }}
      />,
    );

    expect(screen.queryByText("Modifications")).toBeNull();
    const label = screen.getByText("Commits in current code");
    const tile = label.parentElement as HTMLElement;
    expect(tile.textContent).toContain("4");
    expect(tile.getAttribute("title")).toMatch(/current lines, by git blame/);
  });
});
