import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import type { ChangeCandidatesResponse } from "@repowise-dev/types/blast-radius";
import { ReviewNudge } from "../../src/blast-radius/review-nudge.js";

const branch = (label: string): ChangeCandidatesResponse => ({
  working_tree_supported: true,
  default_id: `branch:${label}`,
  changes: [
    {
      id: `branch:${label}`,
      kind: "branch",
      label,
      title: null,
      author: null,
      committed_at: null,
      head_sha: null,
      base: "origin/main",
      ahead: 1,
      files_changed: 2,
      is_current: true,
    },
  ],
});

describe("ReviewNudge", () => {
  it("links a branch by name", () => {
    render(<ReviewNudge data={branch("feat/x")} href={(c) => `/review?change=${c}`} />);
    expect(screen.getByRole("link", { name: /Review/ })).toHaveAttribute("href", "/review?change=feat/x");
  });

  it("keeps the branch prefix for a branch named like the working tree or a sha", () => {
    const sha = "a".repeat(40);
    const { unmount } = render(<ReviewNudge data={branch("wip")} href={(c) => `/review?change=${c}`} />);
    expect(screen.getByRole("link", { name: /Review/ })).toHaveAttribute("href", "/review?change=branch:wip");
    unmount();
    render(<ReviewNudge data={branch(sha)} href={(c) => `/review?change=${c}`} />);
    expect(screen.getByRole("link", { name: /Review/ })).toHaveAttribute("href", `/review?change=branch:${sha}`);
  });
});
