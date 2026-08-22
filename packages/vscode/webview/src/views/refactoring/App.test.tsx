import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, fireEvent, waitFor } from "@testing-library/react";

afterEach(cleanup);
import type { RefactoringPlan } from "@repowise-dev/types/refactoring";
import { App } from "./App";
import type { WebviewHost } from "../../runtime/rpc";

const PLAN: RefactoringPlan = {
  id: "plan-1",
  refactoring_type: "extract_method",
  file_path: "packages/core/src/big.py",
  target_symbol: "process_everything",
  line_start: 40,
  line_end: 120,
  plan: {
    span: { start: 60, end: 90 },
    params: ["items"],
    returns: ["total"],
    suggested_name: "sum_items",
  },
  evidence: { ccn_removed: 7 },
  impact_delta: 1.4,
  effort_bucket: "M",
      blast_radius: { files: ["packages/core/src/caller.py"], file_count: 9 },
  confidence: "high",
  source_biomarker: "complexity",
  rank_score: 0.9,
};

const REPO = {
  id: "r1",
  name: "repo",
  headCommit: "abc",
  defaultBranch: "main",
} as const;

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

    await waitFor(() =>
      expect(refactoringPrompt).toHaveBeenCalledWith("plan-1", "claude-code-mcp"),
    );
    await waitFor(() => expect(copyText).toHaveBeenCalledTimes(1));
    expect(copyText).toHaveBeenCalledWith(
      "GENERATED PROMPT",
      "Plan prompt copied for Claude Code + Repowise MCP.",
    );
  });

  it("renders a performance plan with shared priority and validation handoff", async () => {
    const performance: RefactoringPlan = {
      ...PLAN,
      id: "perf-1",
      refactoring_type: "performance_fix",
      target_symbol: "packages/core/src/db.py::fetch",
      impact_delta: 0,
      benefit: 3.2,
      leverage: 2.1,
      cost: 1.4,
      risk: 2.8,
      plan: {
        opportunity_id: "opp-1",
        strategy: "batch_or_prefetch_io",
        safety: "advisory",
        intervention_symbol: "packages/core/src/db.py::fetch",
        affected_locations: [
          {
            file_path: "packages/core/src/orders.py",
            line_start: 44,
            line_end: 44,
          },
        ],
        affected_locations_total: 7,
        paths: [["packages/core/src/orders.py::load", "packages/core/src/db.py::fetch"]],
        paths_total: 7,
        evidence_truncated: true,
      },
      validation: {
        basis: "inferred",
        via: "call-graph",
        total: 6,
        tests: ["tests/test_orders.py"],
        truncated: true,
        affected_files: ["packages/core/src/orders.py"],
        affected_symbols: ["packages/core/src/db.py::fetch"],
        commands: ["pytest tests/test_orders.py"],
        targets: [],
      },
    };
    const { host } = makeHost({
      refactoringPlan: vi.fn().mockResolvedValue(performance),
    });

    render(<App host={host} repo={REPO} params={{ planId: "perf-1" }} refreshToken={0} />);

    expect(await screen.findByText("Performance")).toBeTruthy();
    expect(screen.getByText("Priority score")).toBeTruthy();
    expect(screen.getByText(/6 guarding tests; 1 shown/)).toBeTruthy();
    expect(screen.getByText("pytest tests/test_orders.py")).toBeTruthy();
    expect(screen.getByText("1 shown of 7 affected call sites.")).toBeTruthy();
    expect(screen.getByText("9 affected files recorded")).toBeTruthy();
    expect(screen.getByText("1 shown of 9 affected files.")).toBeTruthy();
  });
});
