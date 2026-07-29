import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { BacklinksPanel } from "../../src/wiki/backlinks-panel.js";
import type { Backlink } from "../../src/wiki/wiki-links-types.js";

const LONG_TITLE = "File: packages/server/src/repowise/server/routers/overview.py";

function backlink(over: Partial<Backlink> = {}): Backlink {
  return {
    source_page_id: "file_page:a.py",
    source_title: LONG_TITLE,
    source_page_type: "file_page",
    ...over,
  } as Backlink;
}

describe("BacklinksPanel titles", () => {
  it("wraps the title instead of clipping it to one line", () => {
    render(<BacklinksPanel backlinks={[backlink()]} repoId="r1" />);

    const title = screen.getByText(LONG_TITLE);
    // `truncate` is one-line ellipsis. In a ~260px rail it cuts the path at
    // exactly the part that identifies the page, matching the Related list's
    // rule that a title must stay readable.
    expect(title.className).not.toContain("truncate");
  });

  it("names the source page in the link tooltip, not just its kind", () => {
    render(<BacklinksPanel backlinks={[backlink()]} repoId="r1" />);

    expect(screen.getByRole("link")).toHaveAttribute("title", LONG_TITLE);
  });

  it("renders nothing when there are no backlinks", () => {
    const { container } = render(<BacklinksPanel backlinks={[]} repoId="r1" />);
    expect(container).toBeEmptyDOMElement();
  });
});
