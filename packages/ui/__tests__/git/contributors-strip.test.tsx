import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { ContributorsStrip } from "../../src/git/contributors-strip";

const owners = [{ name: "Ann Lee", pct: 100, file_count: 4 }];

describe("contributors strip authorship", () => {
  it("shows unknown as its own band and never derives a human share", () => {
    render(
      <ContributorsStrip
        owners={owners}
        ownersHref="/owners"
        commitsHref="/commits"
        provenance={{
          agentCommits: 2,
          humanAttestedCommits: 0,
          unknownCommits: 98,
          totalCommits: 100,
          agentPctOfCovered: null,
          coveragePct: 2,
          agentNames: [{ name: "claude", count: 2 }],
        }}
      />,
    );

    expect(screen.getAllByText("Unknown").length).toBeGreaterThan(0);
    expect(screen.queryByText("Human")).not.toBeInTheDocument();
    expect(screen.getByText(/coverage 2%/)).toBeInTheDocument();
    expect(screen.queryByText(/of covered/)).not.toBeInTheDocument();
  });
});
