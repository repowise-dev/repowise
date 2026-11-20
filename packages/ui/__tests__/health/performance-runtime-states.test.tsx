import { configure, fireEvent, render, screen, within } from "@testing-library/react";
import { beforeAll, describe, expect, it, vi } from "vitest";
import type { MeasuredFunction, MeasuredResponse } from "@repowise-dev/types/runtime-evidence";

import { PerformanceView } from "../../src/health/performance-view";
import { adapter } from "./fixtures/performance";
import { evidenceAdapter, measured, RUNS, runSummary } from "./fixtures/runtime-evidence";

configure({ asyncUtilTimeout: 5000 });
beforeAll(async () => {
  await import("../../src/health/performance/drawer");
});

function measuredFunction(overrides: Partial<MeasuredFunction> = {}): MeasuredFunction {
  return {
    symbol_id: "src/shared.py::load",
    file_path: "src/shared.py",
    tier: "ranked",
    rank: 1,
    self_share: 0.029,
    total_share: 0.068,
    share_low: 0.025,
    share_high: 0.034,
    samples: 154,
    run_samples: 5312,
    run_id: "run-cli",
    run_count: 2,
    workload_name: "cli build",
    workload_class: "entry_point",
    value_kind: "cpu",
    ordering_only: false,
    freshness: "current",
    evidence_commit_sha: "4f9c2a1e0b",
    measured_at: new Date(Date.now() - 2 * 86_400_000).toISOString(),
    repo_self_share: 0.114,
    wait_share: 0.344,
    library_share: 0.542,
    run_unlisted: 41,
    ...overrides,
  };
}

const unranked = (status: MeasuredResponse["status"], command: string | null): MeasuredResponse => ({
  basis: "unknown",
  status,
  command,
  measured: null,
  measured_in_tests: null,
});

const openFirstCause = async () => {
  const [first] = (await screen.findAllByRole("listitem")).filter((li) =>
    li.hasAttribute("data-performance-opportunity"),
  );
  fireEvent.click(first!);
  return screen.findByRole("dialog");
};

describe("Drawer: runs that measured it", () => {
  it("shows the basis run's share, interval and basis for the cause's function", async () => {
    const getMeasuredFunctions = vi.fn(async () => ({ functions: [measuredFunction()] }));
    render(<PerformanceView adapter={adapter({ ...evidenceAdapter(), getMeasuredFunctions })} />);
    const panel = await openFirstCause();
    expect(await within(panel).findByText("Runs that measured it")).toBeTruthy();
    expect(within(panel).getByText("cli build · entry point")).toBeTruthy();
    expect(within(panel).getByText("2.9%")).toBeTruthy();
    expect(within(panel).getByText("2.5 to 3.4%")).toBeTruthy();
    expect(within(panel).getByText(/154 of 5,312 samples · self time · 4f9c2a1 · current/)).toBeTruthy();
    expect(within(panel).getByText(/Inclusive share in cli build: 6.8%, for context. The rank is by self time./)).toBeTruthy();
    expect(within(panel).getByText(/1 other run also measured it/)).toBeTruthy();
    expect(getMeasuredFunctions).toHaveBeenCalledWith({ symbolId: expect.any(String) });
  });

  it("leaves the section out when no run measured the function", async () => {
    const getMeasuredFunctions = vi.fn(async () => ({ functions: [] }));
    render(<PerformanceView adapter={adapter({ ...evidenceAdapter(), getMeasuredFunctions })} />);
    const panel = await openFirstCause();
    await vi.waitFor(() => expect(getMeasuredFunctions).toHaveBeenCalled());
    expect(within(panel).queryByText("Runs that measured it")).toBeNull();
  });

  it("says an ordering-only run orders within itself, and marks a changed function", async () => {
    const getMeasuredFunctions = vi.fn(async () => ({
      functions: [
        measuredFunction({
          ordering_only: true,
          self_share: null,
          share_low: null,
          share_high: null,
          total_share: null,
          run_count: 1,
          freshness: "stale",
        }),
      ],
    }));
    render(<PerformanceView adapter={adapter({ ...evidenceAdapter(), getMeasuredFunctions })} />);
    const panel = await openFirstCause();
    expect(await within(panel).findByText("rank 1")).toBeTruthy();
    expect(within(panel).getByText("The run orders functions within itself only.")).toBeTruthy();
    expect(within(panel).getByText("changed since")).toBeTruthy();
    expect(within(panel).queryByText(/The rank is by self time/)).toBeNull();
  });

  it("asks about no function when the repository has no stored runs", async () => {
    const getMeasuredFunctions = vi.fn(async () => ({ functions: [measuredFunction()] }));
    render(
      <PerformanceView
        adapter={adapter({
          getMeasured: async () => unranked("no_runtime_data", null),
          getMeasuredFunctions,
        })}
      />,
    );
    const panel = await openFirstCause();
    await within(panel).findByText(/Whether it can be changed/i);
    expect(getMeasuredFunctions).not.toHaveBeenCalled();
  });
});

describe("Runtime evidence notices", () => {
  it("says a run from another commit ranks nothing, with the command", async () => {
    render(
      <PerformanceView
        adapter={adapter({
          getMeasured: async () => unranked("stale", "repowise evidence setup"),
          listEvidenceRuns: async () => ({ ...RUNS, runs: [] }),
        })}
      />,
    );
    expect(await screen.findByText("Measured at another commit.")).toBeTruthy();
    expect(screen.getByText("repowise evidence setup")).toBeTruthy();
  });

  it("says the per-function view waits on an update", async () => {
    render(
      <PerformanceView
        adapter={adapter({
          getMeasured: async () => unranked("needs_update", "repowise update"),
          listEvidenceRuns: async () => ({ ...RUNS, runs: [] }),
        })}
      />,
    );
    expect(
      await screen.findByText("Runs are stored, and the per-function view is built on the next update."),
    ).toBeTruthy();
    expect(screen.getByText("repowise update")).toBeTruthy();
    expect(screen.getByText(/not the same as nothing being slow/)).toBeTruthy();
  });

  it("says when stored runs rank no production function", async () => {
    render(<PerformanceView adapter={adapter({ getMeasured: async () => unranked("not_ranked", null) })} />);
    expect(await screen.findByText("The stored runs rank no production function.")).toBeTruthy();
  });

  it("keeps the code-shape order when only a unit-test run is measured", async () => {
    const getMeasured = vi.fn(async (opts: { runId?: string; limit?: number } = {}) => {
      const view = await measured(opts);
      return { ...view, measured: null };
    });
    render(<PerformanceView adapter={adapter({ getMeasured, listEvidenceRuns: async () => RUNS })} />);
    expect(await screen.findByText("Measured in tests only.")).toBeTruthy();
    expect(screen.queryByRole("heading", { name: /Measured cost in/ })).toBeNull();
    expect(screen.getByRole("button", { name: /Measured in tests/ })).toBeTruthy();
  });

  it("counts what a partly mapped run skipped, by reason", async () => {
    const partial = runSummary({
      run_id: "run-partial",
      workload: "cli build",
      units: { total: 100, matched: 38, skipped: 62, outside_repo: 50, truncated: 0 },
      skipped: { outside_repo: 50, file_changed: 12 },
      mapping_partial: true,
    });
    const evidence = evidenceAdapter();
    render(
      <PerformanceView
        adapter={adapter({ ...evidence, listEvidenceRuns: async () => ({ ...RUNS, runs: [partial, ...RUNS.runs] }) })}
      />,
    );
    expect(await screen.findByText(/38% of entries in/)).toBeTruthy();
    expect(screen.getByText(/Skipped: 50 outside repo, 12 file changed\./)).toBeTruthy();
  });

  it("says nothing when every run ranks and maps", async () => {
    render(<PerformanceView adapter={adapter(evidenceAdapter())} />);
    await screen.findByRole("list", { name: "Measured in cli build" });
    expect(screen.queryByRole("status")).toBeNull();
  });
});
