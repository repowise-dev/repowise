// @vitest-environment jsdom

import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { SWRConfig } from "swr";
import type { FixFirstQueue, FixItem } from "@repowise-dev/types/fix-first";
import type { HealthOverviewResponse } from "@repowise-dev/types/health";
import { TriageView, type CodeHealthAdapter } from "@repowise-dev/ui/health";

const getFixFirst = vi.fn();
const updateFindingStatus = vi.fn();
const getFixFirstItemPrompt = vi.fn();
vi.mock("@/lib/api/code-health", () => ({
  getFixFirst: (...args: unknown[]) => getFixFirst(...args),
  getFixFirstItemPrompt: (...args: unknown[]) => getFixFirstItemPrompt(...args),
  updateFindingStatus: (...args: unknown[]) => updateFindingStatus(...args),
}));

import { FixFirstSection, fixPlanHref } from "./fix-first-section";

afterEach(cleanup);

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
    next_call: {
      purpose: "The full plan: ordered steps, validation and evidence",
      mcp: 'get_health(opportunity_id="refop3_x")',
      cli: null,
      tool: "get_health",
      arguments: { opportunity_id: "refop3_x" },
    },
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
        unknown: 0,
        no_strategy: 0,
        no_plan: 0,
        below_min_worth: 3,
        history_only: 0,
        vendored: 0,
        docs_example: 0,
        deprecated: 0,
        gated_off: 0,
        cold_path: 0,
        unreachable: 0,
        inherent_dispatch: 0,
        small_function: 0,
        no_concrete_step: 0,
        low_value_kind: 0,
        kind_unaudited: 0,
        terminal_flow: 0,
        moderate_quiet: 0,
        unmeasured_cost: 0,
        cold_role: 0,
        background_unproven: 0,
        unconfirmed_cost: 0,
      },
      dormant: 0,
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
  it("opens the stored plan of a plan-led finding by id, and offers nothing without one", () => {
    expect(
      fixPlanHref("r1", item({ kind: "finding", source: { opportunity_id: null, plan_ids: ["refac2_big", "refac2_b"], finding_ids: ["f"] } })),
    ).toBe("/repos/r1/refactoring?plan=refac2_big");
    expect(
      fixPlanHref("r1", item({ kind: "finding", source: { opportunity_id: null, plan_ids: ["a/b c"], finding_ids: ["f"] } })),
    ).toBe("/repos/r1/refactoring?plan=a%2Fb%20c");
  });
});

describe("FixFirstSection", () => {
  it("asks for the production queue first, and the whole one when tests are included", async () => {
    getFixFirst.mockResolvedValue(queue([item({})]));
    renderSection();
    expect(await screen.findByText("1 of 4 eligible items.")).toBeTruthy();
    expect(screen.getByRole("button", { name: "6 excluded" })).toBeTruthy();
    expect(getFixFirst).toHaveBeenCalledWith("r1", { limit: 10, scope: "production" });
    fireEvent.click(screen.getByLabelText("Include tests"));
    await waitFor(() => expect(getFixFirst).toHaveBeenCalledWith("r1", { limit: 10, scope: "all" }));
  });

  it("triages every finding behind an item through the existing per-finding write", async () => {
    getFixFirst.mockResolvedValue(queue([item({})]));
    updateFindingStatus.mockResolvedValue({});
    renderSection();
    // Rows start closed; the actions open with the row.
    fireEvent.click(await screen.findByRole("button", { name: /Extract lines 60-122/ }));
    fireEvent.click(screen.getByRole("button", { name: "Resolved" }));
    await waitFor(() => expect(updateFindingStatus).toHaveBeenCalledTimes(2));
    expect(updateFindingStatus).toHaveBeenCalledWith("r1", "finding_1", "resolved");
    expect(updateFindingStatus).toHaveBeenCalledWith("r1", "finding_2", "resolved");
  });

  it("shows the prompt core renders for the item, in the list's scope", async () => {
    getFixFirst.mockResolvedValue(queue([item({})]));
    getFixFirstItemPrompt.mockResolvedValue({ flavor: "generic", text: "Core prompt text" });
    renderSection();
    fireEvent.click(await screen.findByRole("button", { name: /Extract lines 60-122/ }));
    fireEvent.click(screen.getAllByRole("button", { name: "Copy prompt" })[0]!);
    expect(await screen.findByText("Core prompt text")).toBeTruthy();
    expect(getFixFirstItemPrompt).toHaveBeenCalledWith("r1", "fix1_a", {
      flavor: expect.any(String),
      scope: "production",
    });
  });
});

const FAILED = "Could not save all of that. The list shows what was saved.";

const overview = {
  summary: {
    average_health: 6.8,
    maintainability_average: 8.0,
    hotspot_health: 4.5,
    performance_average: 9.7,
    performance_causes: { now: 4, production: 415, all: 926, per_10k_loc: 9.03 },
    file_count: 3787,
    open_findings: 14755,
  },
} as unknown as HealthOverviewResponse;

describe("FixFirstSection triage re-read", () => {
  it("keeps the score, the scope and the saved note while the overview re-reads", async () => {
    getFixFirst.mockReset().mockResolvedValue(queue([item({})]));
    updateFindingStatus.mockReset().mockResolvedValue({});
    // The first overview read lands; the re-read after the triage is held open.
    let release = () => {};
    const getOverview = vi
      .fn()
      .mockResolvedValueOnce(overview)
      .mockImplementationOnce(
        () => new Promise((resolve) => (release = () => resolve(overview))),
      );
    const adapter = {
      cacheKey: "r1",
      getOverview,
      navigate: vi.fn(),
      renderFileDrawer: () => null,
    } as unknown as CodeHealthAdapter;
    render(
      <SWRConfig value={{ provider: () => new Map(), dedupingInterval: 0 }}>
        <TriageView adapter={adapter} leadSlot={<FixFirstSection repoId="r1" />} />
      </SWRConfig>,
    );
    await screen.findByText("Maintainability");
    fireEvent.click(await screen.findByLabelText("Include tests"));
    await waitFor(() => expect(getFixFirst).toHaveBeenCalledWith("r1", { limit: 10, scope: "all" }));
    fireEvent.click(await screen.findByRole("button", { name: /Extract lines 60-122/ }));
    fireEvent.click(screen.getByRole("button", { name: "Resolved" }));

    await waitFor(() => expect(getOverview).toHaveBeenCalledTimes(2));
    // The overview is refetching, and what it showed is still on screen.
    expect(screen.getByText("Maintainability")).toBeTruthy();
    expect(screen.getByText("6.8")).toBeTruthy();

    release();
    expect(await screen.findByText("Saved as resolved.")).toBeTruthy();
    expect((screen.getByLabelText("Include tests") as HTMLInputElement).checked).toBe(true);
    expect(getFixFirst).toHaveBeenLastCalledWith("r1", { limit: 10, scope: "all" });
  });

  it("re-reads the queue once and reports the failure when one of two writes fails", async () => {
    getFixFirst.mockReset().mockResolvedValue(queue([item({})]));
    updateFindingStatus
      .mockReset()
      .mockImplementation((_repo: string, id: string) =>
        id === "finding_2" ? Promise.reject(new Error("nope")) : Promise.resolve({}),
      );
    renderSection();
    fireEvent.click(await screen.findByRole("button", { name: /Extract lines 60-122/ }));
    expect(getFixFirst).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByRole("button", { name: "Resolved" }));
    expect(await screen.findByText(FAILED)).toBeTruthy();
    expect(updateFindingStatus).toHaveBeenCalledTimes(2);
    expect(getFixFirst).toHaveBeenCalledTimes(2);
    expect(screen.queryByText("Saved as resolved.")).toBeNull();
  });

  it("re-reads nothing when every write fails", async () => {
    getFixFirst.mockReset().mockResolvedValue(queue([item({})]));
    updateFindingStatus.mockReset().mockRejectedValue(new Error("nope"));
    renderSection();
    fireEvent.click(await screen.findByRole("button", { name: /Extract lines 60-122/ }));
    fireEvent.click(screen.getByRole("button", { name: "Resolved" }));
    expect(await screen.findByText(FAILED)).toBeTruthy();
    expect(updateFindingStatus).toHaveBeenCalledTimes(2);
    expect(getFixFirst).toHaveBeenCalledTimes(1);
  });
});
