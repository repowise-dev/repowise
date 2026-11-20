import { render } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { setDisplayTimeZone } from "../../src/lib/format.js";
import { OriginBlock } from "../../src/stats/origin-block.js";

describe("OriginBlock", () => {
  afterEach(() => setDisplayTimeZone("UTC"));

  it("shows the root commit's UTC day whatever the viewer's zone", () => {
    setDisplayTimeZone("America/Los_Angeles");
    const { container } = render(
      <OriginBlock
        data={{
          first_commit_at: "2024-03-01T02:00:00Z",
          first_commit_subject: "initial commit",
          first_commit_author: "Ada",
          last_commit_at: null,
          age_days: null,
          total_commits: 1,
          contributor_count: 1,
        }}
      />,
    );
    // 02:00 UTC on Mar 1 is still Feb 29 in Los Angeles.
    expect(container.textContent).toContain("Root commit, Mar 1, 2024");
  });
});
