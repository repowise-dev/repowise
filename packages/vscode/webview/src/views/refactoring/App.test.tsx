import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, fireEvent, waitFor } from "@testing-library/react";

afterEach(cleanup);
import type { RefactoringPlan } from "@repowise-dev/ui/refactoring/types";
import { App } from "./App";
import type { WebviewHost } from "../../runtime/rpc";

const PLAN: RefactoringPlan = {
  id: "plan-1",
  refactoring_type: "extract_method",
  file_path: "packages/core/src/big.py",
  target_symbol: "process_everything",
  line_start: 40,
  line_end: 120,
  plan: { span: { start: 60, end: 90 }, params: ["items"], returns: ["total"], suggested_name: "sum_items" },
  evidence: { ccn_removed: 7 },
  impact_delta: 1.4,
  effort_bucket: "M",
  blast_radius: { files: ["packages/core/src/caller.py"], file_count: 1 },
  confidence: "high",
  source_biomarker: "complexity",
  rank_score: 0.9,
};

const REPO = { id: "r1", name: "repo", headCommit: "abc", defaultBranch: "main" } as const;

function makeHost(overrides: Partial<WebviewHost["api"]> = {}): {
  host: WebviewHost;
  refactoringPrompt: ReturnType<typeof vi.fn>;
  copyText: ReturnType<typeof vi.fn>;
} {
  const refactoringPrompt = vi.fn().mockResolvedValue("GENERATED PROMPT");
  const copyText = vi.fn();
  const api = {
    refactoringPlan: vi.fn().mockResolvedValue(PLAN),
    refactoringPrompt,
    ...overrides,
  } as unknown as WebviewHost["api"];
  const host = {
    api,
    onInit: () => () => {},
    onRefresh: () => () => {},
    ready: () => {},
    openFile: vi.fn(),
    copyText,
    openExternal: () => {},
  } as unknown as WebviewHost;
  return { host, refactoringPrompt, copyText };
}

describe("refactoring App detail page", () => {
  it("renders the plan header and copies a flavored prompt on click", async () => {
    const { host, refactoringPrompt, copyText } = makeHost();

    render(<App host={host} repo={REPO} params={{ planId: "plan-1" }} refreshToken={0} />);

    // Header renders from the fetched plan.
    await screen.findByRole("heading", { name: "process_everything" });
    expect(screen.getByText("Extract Method")).toBeTruthy();

    // Clicking a flavor button builds that flavor's prompt and copies it.
    fireEvent.click(screen.getByRole("button", { name: "Claude Code + Repowise MCP" }));

    await waitFor(() => expect(refactoringPrompt).toHaveBeenCalledWith("plan-1", "claude-code-mcp"));
    await waitFor(() => expect(copyText).toHaveBeenCalledTimes(1));
    expect(copyText).toHaveBeenCalledWith(
      "GENERATED PROMPT",
      "Plan prompt copied for Claude Code + Repowise MCP.",
    );
  });
});
