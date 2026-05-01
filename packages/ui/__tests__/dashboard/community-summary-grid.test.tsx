import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { CommunitySummaryGrid } from "../../src/dashboard/community-summary-grid.js";
import type {
  CommunityDetail,
  CommunitySummaryItem,
} from "@repowise-dev/types/graph";

const summaries: CommunitySummaryItem[] = [
  { community_id: 1, label: "auth", cohesion: 0.84, member_count: 12, top_file: "auth/index.ts" },
  { community_id: 2, label: "billing", cohesion: 0.62, member_count: 7, top_file: "billing/api.ts" },
];

const detail: CommunityDetail = {
  community_id: 1,
  label: "auth",
  cohesion: 0.84,
  member_count: 2,
  members: [
    { path: "auth/index.ts", pagerank: 0.0123, is_entry_point: true },
    { path: "auth/jwt.ts", pagerank: 0.0089, is_entry_point: false },
  ],
  truncated: false,
  neighboring_communities: [
    { community_id: 2, label: "billing", cross_edge_count: 3 },
  ],
};

describe("CommunitySummaryGrid", () => {
  it("renders one row per summary with cohesion + member count", () => {
    render(<CommunitySummaryGrid communities={summaries} />);
    expect(screen.getByText("auth")).toBeInTheDocument();
    expect(screen.getByText("12 files")).toBeInTheDocument();
    expect(screen.getByText("billing")).toBeInTheDocument();
  });

  it("renders nothing when communities is empty", () => {
    const { container } = render(<CommunitySummaryGrid communities={[]} />);
    expect(container.firstChild).toBeNull();
  });

  it("calls onExpand and renders detail when supplied", () => {
    const onExpand = vi.fn();
    render(
      <CommunitySummaryGrid
        communities={summaries}
        details={{ 1: detail }}
        onExpand={onExpand}
      />,
    );
    fireEvent.click(screen.getByText("auth"));
    expect(onExpand).toHaveBeenCalledWith(1);
    expect(screen.getByText("Connected Communities")).toBeInTheDocument();
  });
});
