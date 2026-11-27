import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import type {
  RefactoringOpportunity,
  RefactoringOpportunityPage,
  RefactoringPlan,
} from "@repowise-dev/types/refactoring";
import { App } from "./App";
import type { WebviewHost } from "../../runtime/rpc";

afterEach(cleanup);

const REPO = { id: "r1", name: "repo", headCommit: "abc", defaultBranch: "main" } as const;

const ROW: RefactoringOpportunity = {
  opportunity_id: "refop2_row",
  refactoring_model_version: 2,
  status: "open",
  file_path: "packages/core/src/big.py",
  lead_biomarker: "nested_complexity",
  lead_refactoring_type: "split_file",
  addresses_primary_problem: true,
  effort_bucket: "M",
  confidence: "high",
  step_count: 3,
  mechanical_steps: 2,
  judgment_steps: 1,
  evidence_total: 4,
  affected_files_total: 2,
  recoverable_health: 1.4,
  rank_score: 1.45,
  rank_position: 1,
  queue_position: 1,
  rank_factors: {},
  why_ranked: [],
};

const PAGE = {
  items: [ROW],
  total: 1,
  next_offset: null,
  scope: "fix_first",
  summary: null,
  facets: { lead_type: { split_file: 1 } },
} as unknown as RefactoringOpportunityPage;

const PLAN = {
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
  blast_radius: { files: [], file_count: 0 },
  confidence: "high",
  source_biomarker: "complexity",
  rank_score: 0.9,
} as unknown as RefactoringPlan;

function makeHost(overrides: Record<string, unknown> = {}) {
  const api = {
    refactoringOpportunities: vi.fn().mockResolvedValue(PAGE),
    refactoringOpportunity: vi.fn().mockResolvedValue({ found: false }),
    refactoringOpportunityPrompt: vi.fn().mockResolvedValue("OPPORTUNITY PROMPT"),
    refactoringPlan: vi.fn().mockResolvedValue(PLAN),
    refactoringPrompt: vi.fn().mockResolvedValue("PLAN PROMPT"),
    setRefactoringOpportunityStatus: vi.fn().mockResolvedValue(undefined),
    fileContent: vi.fn().mockResolvedValue(""),
    ...overrides,
  };
  const host = { api, openFile: vi.fn(), copyText: vi.fn() } as unknown as WebviewHost;
  return { host, api };
}

describe("refactoring panel", () => {
  it("renders the web board over the board query", async () => {
    const { host, api } = makeHost();
    render(<App host={host} repo={REPO} params={{}} refreshToken={0} />);

    expect((await screen.findAllByText("big.py")).length).toBeGreaterThan(0);
    expect(api.refactoringOpportunities).toHaveBeenCalledWith(
      expect.objectContaining({ status: "open", scope: "fix_first", order: "queue", stepPreview: 0, offset: 0 }),
    );
  });

  it("seeds the search with the file a CodeLens opened it for", async () => {
    const { host, api } = makeHost();
    render(<App host={host} repo={REPO} params={{ filePath: "src/a.py" }} refreshToken={0} />);
    await waitFor(() =>
      expect(api.refactoringOpportunities).toHaveBeenCalledWith(expect.objectContaining({ search: "src/a.py", scope: "all" })),
    );
  });

  it("opens the agent prompt from the row and loads the server's prompt", async () => {
    const { host, api } = makeHost();
    render(<App host={host} repo={REPO} params={{}} refreshToken={0} />);

    fireEvent.click(await screen.findByRole("button", { name: `Agent prompt: ${ROW.file_path}` }));
    await waitFor(() =>
      expect(api.refactoringOpportunityPrompt).toHaveBeenCalledWith(ROW.opportunity_id, expect.any(String)),
    );
    expect(await screen.findByText("AI refactoring prompt")).toBeTruthy();
  });

  it("replaces the rows with the error when a read fails", async () => {
    const { host } = makeHost({ refactoringOpportunities: vi.fn().mockRejectedValue(new Error("down")) });
    render(<App host={host} repo={REPO} params={{}} refreshToken={0} />);
    expect(await screen.findByText("Couldn't load refactoring")).toBeTruthy();
  });

  it("opens a plan passed by id in the plan inspector", async () => {
    const { host, api } = makeHost();
    render(<App host={host} repo={REPO} params={{ planId: "plan-1" }} refreshToken={0} />);
    await waitFor(() => expect(api.refactoringPlan).toHaveBeenCalledWith("plan-1"));
    expect((await screen.findAllByText(/process_everything/)).length).toBeGreaterThan(0);
  });
});
