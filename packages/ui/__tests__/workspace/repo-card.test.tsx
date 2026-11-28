import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";

import { RepoCard } from "../../src/workspace/repo-card.js";

describe("RepoCard", () => {
  it("names the figure as doc confidence, floored", () => {
    render(
      <RepoCard
        repoId="r1"
        alias="api"
        name="api"
        path="./api"
        isPrimary={false}
        stats={{ file_count: 12, doc_coverage_pct: 99.8 }}
        gitSummary={{ hotspot_count: 3 }}
      />,
    );
    expect(screen.getByText("99% doc confidence")).toBeInTheDocument();
    expect(screen.queryByText(/% docs$/)).toBeNull();
  });
});
