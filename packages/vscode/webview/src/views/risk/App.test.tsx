import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import type { BlastRadiusResponse, ChangeCandidatesResponse } from "@repowise-dev/types/blast-radius";
import { App } from "./App";
import type { WebviewHost } from "../../runtime/rpc";

afterEach(cleanup);

const REPO = { id: "r1", name: "repo", headCommit: "abc", defaultBranch: "main" } as const;

const CHANGES: ChangeCandidatesResponse = {
  working_tree_supported: true,
  health_check_supported: true,
  default_id: "branch:feat/x",
  changes: [
    {
      id: "branch:feat/x",
      kind: "branch",
      label: "feat/x",
      title: "feat: x",
      author: "Dev",
      committed_at: null,
      head_sha: "a".repeat(40),
      base: "origin/main",
      ahead: 1,
      files_changed: 1,
      is_current: true,
    },
  ],
};

const RESULT = {
  structural_impact_score: 0.4,
  structural_impact_band: "localized",
  overall_risk_score: 0.4,
  direct_risks: [],
  transitive_affected: [],
  cochange_warnings: [],
  recommended_reviewers: [],
  test_gaps: [],
} as unknown as BlastRadiusResponse;

function makeHost() {
  const api = {
    hotspots: vi.fn().mockResolvedValue([]),
    searchNodes: vi.fn().mockResolvedValue([]),
    blastRadius: vi.fn().mockResolvedValue(RESULT),
    reviewChanges: vi.fn().mockResolvedValue(CHANGES),
    changeBlastRadius: vi.fn().mockResolvedValue({ ...RESULT, changed_files: ["src/a.ts"] }),
    changeHealthCheck: vi.fn(),
  };
  return { host: { api, openFile: vi.fn() } as unknown as WebviewHost, api };
}

describe("change risk panel", () => {
  it("is the web Review page, opening the change the server ranks first", async () => {
    const { host, api } = makeHost();
    render(<App host={host} repo={REPO} params={{}} refreshToken={0} />);

    expect(screen.getByRole("heading", { name: "Review" })).toBeTruthy();
    await waitFor(() => expect(api.reviewChanges).toHaveBeenCalled());
    await waitFor(() =>
      expect(api.changeBlastRadius).toHaveBeenCalledWith(expect.objectContaining({ change_id: "branch:feat/x" })),
    );
  });
});
