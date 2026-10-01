// @vitest-environment jsdom

import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { SWRConfig } from "swr";
import type { FixFirstQueue, FixItem } from "@repowise-dev/types/fix-first";

const getFixFirst = vi.fn();
const updateFindingStatus = vi.fn();
vi.mock("@/lib/api/code-health", () => ({
  getFixFirst: (...args: unknown[]) => getFixFirst(...args),
  updateFindingStatus: (...args: unknown[]) => updateFindingStatus(...args),
}));

import { FixFirstSection, fixPlanHref } from "./fix-first-section";

function item(overrides: Partial<FixItem>): FixItem {
  return {
    id: "fix1_a",
    rank: 0,
    tier: "now",
    kind: "refactor",
    improves: "defect",
    title: "Extract lines 60-122 of quick_repo_scan into compute_info",
    target: { file_path: "src/scan.py", symbol: "quick_repo_scan", line_start: 60, line_end: 133 },
    why: "quick_repo_scan: CCN 15, 44 lines.",
    facts: [],
    action: { summary: "1 step", steps: [], steps_total: 0, mechanical: true },
    gain: { kind: "health_points", value: 1.7, text: "+1.7 health on this file" },
    effort: { bucket: "S", basis: "sized by the stored plan" },
    risk: { level: "low", dependents: 4, files_touched: 1, text: "Touches 1 file." },
    confidence: { level: "high", reason: "proven mechanical" },
    verify: { tests: [], tests_total: 0, command: null, basis: "unknown" },
    context: [],
    source: { opportunity_id: "refop3_x", plan_ids: [], finding_ids: ["finding_1", "finding_2"] },
    next_call: { tool: "get_health", arguments: { opportunity_id: "refop3_x" } },
    why_ranked: [],
    ...overrides,
  };
}

function queue(items: FixItem[]): FixFirstQueue {
  return {
    items,
    lead: items[0] ?? null,
    totals: {
      candidates: 10,
      eligible: 4,
      shown: items.length,
      excluded: {
        test: 3,
        tooling: 0,
        generated: 0,
        expected: 0,
        no_plan: 0,
        below_min_worth: 3,
        history_only: 0,
      },
    },
    by_improves: { defect: 4, maintainability: 0, performance: 0 },
    model_version: 1,
    basis: { analyzed_commit: null, health_analyzed_at: null },
  };
}

function renderSection() {
  return render(
    <SWRConfig value={{ provider: () => new Map(), dedupingInterval: 0 }}>
      <FixFirstSection repoId="r1" />
    </SWRConfig>,
  );
}

describe("fixPlanHref", () => {
  it("opens a refactoring plan on Refactoring and a performance fix on Performance", () => {
    expect(fixPlanHref("r1", item({}))).toBe("/repos/r1/refactoring?opportunity=refop3_x");
    expect(
      fixPlanHref("r1", item({ kind: "perf_fix", source: { opportunity_id: "perf2_y", plan_ids: [], finding_ids: [] } })),
    ).toBe("/repos/r1/code-health?tab=performance&opportunity=perf2_y");
    expect(
      fixPlanHref("r1", item({ kind: "finding", source: { opportunity_id: null, plan_ids: [], finding_ids: ["f"] } })),
    ).toBeNull();
  });
});

describe("FixFirstSection", () => {
  it("asks for the production queue first, and the whole one when tests are included", async () => {
    getFixFirst.mockResolvedValue(queue([item({})]));
    renderSection();
    expect(await screen.findByText(/1 of 4 eligible items\. Excluded: 3 in tests/)).toBeTruthy();
    expect(getFixFirst).toHaveBeenCalledWith("r1", { limit: 10, scope: "production" });
    fireEvent.click(screen.getByLabelText("Include tests"));
    await waitFor(() => expect(getFixFirst).toHaveBeenCalledWith("r1", { limit: 10, scope: "all" }));
  });

  it("triages every finding behind an item through the existing per-finding write", async () => {
    getFixFirst.mockResolvedValue(queue([item({})]));
    updateFindingStatus.mockResolvedValue({});
    renderSection();
    fireEvent.click(await screen.findByRole("button", { name: "Resolved" }));
    await waitFor(() => expect(updateFindingStatus).toHaveBeenCalledTimes(2));
    expect(updateFindingStatus).toHaveBeenCalledWith("r1", "finding_1", "resolved");
    expect(updateFindingStatus).toHaveBeenCalledWith("r1", "finding_2", "resolved");
  });
});
