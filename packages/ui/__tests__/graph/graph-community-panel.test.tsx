import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { GraphCommunityPanel } from "../../src/graph/graph-community-panel.js";
import type { CommunityDetail } from "@repowise-dev/types/graph";

const sampleCommunity: CommunityDetail = {
  community_id: 7,
  label: "auth-cluster",
  cohesion: 0.82,
  member_count: 3,
  members: [
    { path: "src/auth/login.ts", pagerank: 0.05, is_entry_point: true },
    { path: "src/auth/session.ts", pagerank: 0.03, is_entry_point: false },
    { path: "src/auth/utils.ts", pagerank: 0.01, is_entry_point: false },
  ],
  truncated: false,
  neighboring_communities: [
    { community_id: 4, label: "db-cluster", cross_edge_count: 12 },
  ],
};

const withState: CommunityDetail = {
  ...sampleCommunity,
  health_score: 5.2,
  scored_member_count: 2,
  hot_count: 1,
  dead_count: 1,
  decision_count: 1,
  primary_owner: "Ada",
  primary_owner_file_count: 2,
  members: [
    { path: "src/auth/login.ts", pagerank: 0.05, is_entry_point: true, is_hotspot: true },
    { path: "src/auth/session.ts", pagerank: 0.03, is_entry_point: false, is_dead: true },
    { path: "src/auth/utils.ts", pagerank: 0.01, is_entry_point: false },
  ],
};

/** Open a `CollapsibleSection` by its toggle label. */
function expand(title: string) {
  fireEvent.click(screen.getByRole("button", { name: new RegExp(title) }));
}

describe("GraphCommunityPanel", () => {
  it("names the community and says the grouping was automatic", () => {
    render(
      <GraphCommunityPanel
        communityId={7}
        community={sampleCommunity}
        isLoading={false}
        onClose={vi.fn()}
      />,
    );
    expect(screen.getByText("auth-cluster")).toBeTruthy();
    expect(screen.getByText("3 files, grouped automatically")).toBeTruthy();
  });

  it("shows the enter affordance only when the callback is provided", () => {
    const onEnter = vi.fn();
    const { rerender } = render(
      <GraphCommunityPanel
        communityId={7}
        community={sampleCommunity}
        isLoading={false}
        onClose={vi.fn()}
      />,
    );
    expect(screen.queryByText("Enter this community")).toBeNull();

    rerender(
      <GraphCommunityPanel
        communityId={7}
        community={sampleCommunity}
        isLoading={false}
        onClose={vi.fn()}
        onEnterCommunity={onEnter}
      />,
    );
    screen.getByText("Enter this community").click();
    expect(onEnter).toHaveBeenCalledTimes(1);
  });

  it("falls back to a numeric label when community is null", () => {
    render(
      <GraphCommunityPanel
        communityId={42}
        community={null}
        isLoading={false}
        onClose={vi.fn()}
      />,
    );
    expect(screen.getByText("Community 42")).toBeTruthy();
    expect(screen.getByText("Community not found")).toBeTruthy();
  });
});

describe("GraphCommunityPanel reading order", () => {
  it("opens with the state above the fold and the long lists collapsed", () => {
    // The complaint this structure answers: everything used to be expanded, so
    // the figure that says whether the area is in trouble sat one screen above
    // thirty member rows and ten neighbour rows.
    render(
      <GraphCommunityPanel
        communityId={7}
        community={withState}
        isLoading={false}
        onClose={vi.fn()}
      />,
    );
    // Lede and facts are rendered.
    expect(screen.getByText("5.2")).toBeTruthy();
    expect(screen.getByText("Changes often")).toBeTruthy();
    // The lists are behind a disclosure, so no member or neighbour is drawn.
    expect(screen.queryByText("src/auth/login.ts")).toBeNull();
    expect(screen.queryByText("db-cluster")).toBeNull();
  });

  it("counts each collapsed list on its own toggle", () => {
    render(
      <GraphCommunityPanel
        communityId={7}
        community={withState}
        isLoading={false}
        onClose={vi.fn()}
      />,
    );
    // A closed section still has to say how much is inside it, or collapsing is
    // just hiding.
    expect(screen.getByRole("button", { name: /Members/ }).textContent).toContain("3");
    expect(screen.getByRole("button", { name: /Talks to/ }).textContent).toContain("1");
  });

  it("reveals the members on demand", () => {
    render(
      <GraphCommunityPanel
        communityId={7}
        community={withState}
        isLoading={false}
        onClose={vi.fn()}
        memberHref={(p) => `/files/${p}`}
      />,
    );
    expand("Members");
    expect(screen.getByText("src/auth/login.ts")).toBeTruthy();
    expect(screen.getByText("src/auth/utils.ts")).toBeTruthy();
  });
});

describe("GraphCommunityPanel state", () => {
  it("leads with the health figure, its band, and how much of the group it covers", () => {
    render(
      <GraphCommunityPanel
        communityId={7}
        community={withState}
        isLoading={false}
        onClose={vi.fn()}
      />,
    );
    expect(screen.getByText("5.2")).toBeTruthy();
    // Five-band vocabulary, shared with the Code Health lede and the file
    // health drawer, so one score never gets two sets of words.
    expect(screen.getByText("Fair")).toBeTruthy();
    // The mean is over the scored members, and says so rather than implying it
    // measured all three.
    expect(screen.getByText(/2 of 3 files that are\s+scored/)).toBeTruthy();
  });

  it("never renders a missing health score as a number", () => {
    render(
      <GraphCommunityPanel
        communityId={7}
        community={{ ...sampleCommunity, health_score: null }}
        isLoading={false}
        onClose={vi.fn()}
      />,
    );
    expect(screen.getByText("Not scored")).toBeTruthy();
    expect(screen.queryByText("0.0")).toBeNull();
  });

  it("puts the signal counts in the fact grid, greying the zeroes", () => {
    render(
      <GraphCommunityPanel
        communityId={7}
        community={{ ...withState, hot_count: 4, dead_count: 0 }}
        isLoading={false}
        onClose={vi.fn()}
      />,
    );
    expect(screen.getByText("4 files")).toBeTruthy();
    // A zero is still news, so it is stated rather than dropped.
    expect(screen.getByText("none")).toBeTruthy();
  });

  it("expands the entry-point badge instead of abbreviating it", () => {
    render(
      <GraphCommunityPanel
        communityId={7}
        community={withState}
        isLoading={false}
        onClose={vi.fn()}
      />,
    );
    expand("Members");
    expect(screen.getByText("Entry")).toBeTruthy();
    expect(screen.queryByText("EP")).toBeNull();
  });

  it("makes a neighbour row navigable when the host can navigate", () => {
    const onNeighborSelect = vi.fn();
    const { rerender } = render(
      <GraphCommunityPanel
        communityId={7}
        community={withState}
        isLoading={false}
        onClose={vi.fn()}
      />,
    );
    expand("Talks to");
    // Without a handler the row is text, not a button that does nothing — the
    // bug this replaced.
    expect(screen.queryByRole("button", { name: /db-cluster/ })).toBeNull();

    rerender(
      <GraphCommunityPanel
        communityId={7}
        community={withState}
        isLoading={false}
        onClose={vi.fn()}
        onNeighborSelect={onNeighborSelect}
      />,
    );
    // No second expand: the section keeps its own open state across a
    // rerender, and toggling again would close it.
    screen.getByRole("button", { name: /db-cluster/ }).click();
    expect(onNeighborSelect).toHaveBeenCalledWith(4);
  });

  it("sends a flagged member to the page that explains the flag", () => {
    render(
      <GraphCommunityPanel
        communityId={7}
        community={withState}
        isLoading={false}
        onClose={vi.fn()}
        healthHrefFor={(p) => `/health?file=${p}`}
        deadCodeHref="/dead"
      />,
    );
    expand("Members");
    const hot = screen.getByLabelText(
      "src/auth/login.ts is a churn hotspot; open it in Code Health",
    );
    expect(hot.getAttribute("href")).toBe("/health?file=src/auth/login.ts");
    const dead = screen.getByLabelText(
      "src/auth/session.ts is flagged unreachable; open the dead-code findings",
    );
    expect(dead.getAttribute("href")).toBe("/dead");
  });
});

describe("GraphCommunityPanel truthfulness", () => {
  it("reconciles a count over every member with a list showing only a page", () => {
    render(
      <GraphCommunityPanel
        communityId={7}
        community={{
          ...withState,
          member_count: 500,
          truncated: true,
          hot_count: 12,
        }}
        isLoading={false}
        onClose={vi.fn()}
      />,
    );
    // The toggle carries the ratio while closed...
    expect(screen.getByRole("button", { name: /Members/ }).textContent).toContain(
      "3 of 500",
    );
    // ...and the opened list says which figure covers what, because counting
    // four flames under a headline of twelve is otherwise unexplained.
    expand("Members");
    expect(
      screen.getByText("The 3 most connected. The counts above cover all 500."),
    ).toBeTruthy();
  });

  it("does not render a cohesion figure for a community that has no pairs", () => {
    // `_cohesion_score` returns 1.0 for n <= 1 by definition, so the raw value
    // would read as a perfect score beside a tooltip about file pairs.
    render(
      <GraphCommunityPanel
        communityId={7}
        community={{ ...sampleCommunity, member_count: 1, cohesion: 1 }}
        isLoading={false}
        onClose={vi.fn()}
      />,
    );
    expect(screen.queryByText("Cohesion")).toBeNull();
    expect(screen.queryByText("100.0%")).toBeNull();
  });

  it("states cohesion as a labelled figure for a community that has pairs", () => {
    render(
      <GraphCommunityPanel
        communityId={7}
        community={sampleCommunity}
        isLoading={false}
        onClose={vi.fn()}
      />,
    );
    expect(screen.getByText("Cohesion")).toBeTruthy();
    expect(screen.getByText("82.0%")).toBeTruthy();
  });
});

describe("GraphCommunityPanel population and shape", () => {
  it("reads conductance as the share that stays inside, and says what is hidden", () => {
    render(
      <GraphCommunityPanel
        communityId={7}
        community={{ ...sampleCommunity, conductance: 0.28, hidden_member_count: 393 }}
        isLoading={false}
        onClose={vi.fn()}
      />,
    );
    expect(screen.getByText("Stays inside")).toBeTruthy();
    expect(screen.getByText("72%")).toBeTruthy();
    expect(screen.queryByText("Cohesion")).toBeNull();
    expect(
      screen.getByText("3 files, grouped automatically · 393 hidden by the file filter"),
    ).toBeTruthy();
  });

  it("falls back to cohesion on an index without conductance", () => {
    render(
      <GraphCommunityPanel
        communityId={7}
        community={sampleCommunity}
        isLoading={false}
        onClose={vi.fn()}
      />,
    );
    expect(screen.getByText("Cohesion")).toBeTruthy();
    expect(screen.getByText("82.0%")).toBeTruthy();
    expect(screen.queryByText("Stays inside")).toBeNull();
  });
});
