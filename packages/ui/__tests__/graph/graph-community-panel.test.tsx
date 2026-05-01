import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
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

describe("GraphCommunityPanel", () => {
  it("renders community label, members, and neighbors", () => {
    render(
      <GraphCommunityPanel
        communityId={7}
        community={sampleCommunity}
        isLoading={false}
        onClose={vi.fn()}
      />,
    );
    expect(screen.getByText("auth-cluster")).toBeTruthy();
    expect(screen.getByText("Members (3)")).toBeTruthy();
    expect(screen.getByText("db-cluster")).toBeTruthy();
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
