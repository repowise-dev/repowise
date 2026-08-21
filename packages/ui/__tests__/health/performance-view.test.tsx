import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeAll, describe, expect, it, vi } from "vitest";
import type { PerformanceOpportunityPage } from "@repowise-dev/types/health";
import type { RefactoringPlan } from "@repowise-dev/types/refactoring";

import { PerformanceView, type PerformanceViewAdapter } from "../../src/health/performance-view";

beforeAll(() => {
  Element.prototype.scrollIntoView = vi.fn();
});

function page(planId: string | null = "plan-1"): PerformanceOpportunityPage {
  return {
    items: [
      {
        opportunity_id: "opp-1",
        biomarker_type: "io_in_loop",
        biomarker_types: ["io_in_loop"],
        boundary_kind: "db",
        execution_context: "production",
        terminal_sink: "src/db.py::fetch",
        shared_path_suffix: ["src/shared.py::load", "src/db.py::fetch"],
        intervention_symbol: "src/shared.py::load",
        affected_call_sites_total: 2,
        affected_files_total: 2,
        observations_total: 6,
        evidence: [
          {
            finding_id: "finding-1",
            file_path: "src/a.py",
            biomarker_type: "io_in_loop",
            function_name: "run",
            line_start: 10,
            line_end: 10,
            reason: "Database work repeats.",
            path: ["src/a.py::run", "src/shared.py::load", "src/db.py::fetch"],
            provenance: "reliable-edge",
          },
          {
            finding_id: "finding-2",
            file_path: "src/b.py",
            biomarker_type: "io_in_loop",
            function_name: "run",
            line_start: 20,
            line_end: 20,
            reason: "Database work repeats.",
            path: ["src/b.py::run", "src/shared.py::load", "src/db.py::fetch"],
            provenance: "reliable-edge",
          },
        ],
        evidence_truncated: true,
        reliable_entry_reachability: true,
        provenance: "reliable-edge",
        confidence: "medium",
        rank_score: 12.5,
        rank_factors: {},
        fix: {
          strategy: "batch_or_prefetch_io",
          safety: "advisory",
          rationale: "Validate result equivalence.",
        },
        plan_id: planId,
        plan_status: planId ? "available" : "no_safe_plan",
        plan_reason: planId ? "Exact match." : "No coherent intervention was proven.",
      },
    ],
    total: 1,
    has_more: false,
    next_offset: null,
    summary: {
      total: 2,
      production_total: 1,
      tooling_total: 0,
      test_total: 1,
      with_plan_total: planId ? 1 : 0,
      without_plan_total: planId ? 1 : 2,
    },
  };
}

function adapter(load = vi.fn(async () => page())): PerformanceViewAdapter {
  return {
    cacheKey: `repo-${Math.random()}`,
    listFindings: vi.fn(async () => []),
    getPerformanceOpportunities: load,
    getPerformanceOpportunityFindings: vi.fn(),
    refactoringPlanHref: (planId) => `/refactoring?plan=${planId}`,
    fileHref: (path) => `/files/${path}`,
    symbolHref: (symbol) => `/symbols/${symbol}`,
    navigate: vi.fn(),
  };
}

describe("PerformanceView", () => {
  it("renders causal totals, context, provenance paths, truncation, and an exact plan handoff", async () => {
    render(<PerformanceView adapter={adapter()} />);
    const row = await screen.findByRole("button", { name: /2 call sites/i });
    expect(screen.getByRole("tab", { name: /Production & tooling 1/i })).toBeTruthy();
    expect(screen.getByRole("tab", { name: /Test suite 1/i })).toBeTruthy();

    fireEvent.click(row);
    expect(await screen.findAllByText("Reliable resolved edge")).toHaveLength(2);
    expect(screen.getByText("2 paths shown of 6 recorded observations.")).toBeTruthy();
    expect(screen.getByText(/Database · Production · Medium confidence/)).toBeTruthy();
    expect(screen.getByRole("link", { name: "Open on Refactoring page" }).getAttribute("href")).toBe(
      "/refactoring?plan=plan-1",
    );
  });

  it("switches server-owned context and explains the no-safe-plan state", async () => {
    const load = vi.fn(async () => page(null));
    render(<PerformanceView adapter={adapter(load)} />);
    fireEvent.click(await screen.findByRole("tab", { name: /Test suite 1/i }));
    await waitFor(() =>
      expect(load).toHaveBeenLastCalledWith({
        context: "test",
        offset: 0,
        limit: 20,
      }),
    );
    expect(load).toHaveBeenCalledTimes(2);

    fireEvent.click(screen.getByRole("button", { name: /2 call sites/i }));
    expect(await screen.findByText(/No safe structured plan/)).toBeTruthy();
    expect(screen.queryByRole("link", { name: "Open on Refactoring page" })).toBeNull();
  });

  it("distinguishes a safe plan that needs reindexing from no safe plan", async () => {
    const result = page(null);
    result.items[0] = {
      ...result.items[0]!,
      plan_status: "not_persisted",
      plan_reason: "A matching recommendation can be materialized by reindexing.",
    };
    render(<PerformanceView adapter={adapter(vi.fn(async () => result))} />);

    fireEvent.click(await screen.findByRole("button", { name: /2 call sites/i }));
    expect(await screen.findByText("Refresh the index to materialize the plan")).toBeTruthy();
    expect(screen.queryByText("No safe structured plan")).toBeNull();
  });

  it("opens the canonical refactoring drawer and agent handoff for an exact plan", async () => {
    const exactPlan: RefactoringPlan = {
      id: "plan-1",
      refactoring_type: "performance_fix",
      file_path: "src/shared.py",
      target_symbol: "src/shared.py::load",
      line_start: 8,
      line_end: 12,
      plan: {
        opportunity_id: "opp-1",
        strategy: "batch_or_prefetch_io",
        safety: "advisory",
        intervention_symbol: "src/shared.py::load",
        affected_locations: [],
        affected_locations_total: 2,
        paths: [["src/a.py::run", "src/shared.py::load"]],
        paths_total: 1,
        evidence_truncated: false,
      },
      evidence: {},
      impact_delta: 0,
      effort_bucket: "M",
      blast_radius: { file_count: 2, files: ["src/a.py", "src/b.py"] },
      confidence: "medium",
      source_biomarker: "io_in_loop",
      rank_score: 12.5,
      benefit: 4,
      leverage: 3,
      cost: 2,
      risk: 1,
    };
    const exact = adapter();
    exact.getRefactoringPlan = vi.fn(async () => exactPlan);
    render(<PerformanceView adapter={exact} />);

    fireEvent.click(await screen.findByRole("button", { name: /Inspect 2 call sites/i }));
    expect(await screen.findByText("The change")).toBeTruthy();
    expect(screen.getByText("Causal context")).toBeTruthy();
    expect(screen.getByRole("button", { name: /Review raw findings/ })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Copy prompt for an agent" }));
    expect(await screen.findByText("AI performance plan")).toBeTruthy();
    expect(exact.getRefactoringPlan).toHaveBeenCalledWith("plan-1");
  });

  it("rejects a fetched plan whose stable opportunity identity does not match", async () => {
    const unrelated: RefactoringPlan = {
      id: "plan-1",
      refactoring_type: "performance_fix",
      file_path: "src/other.py",
      target_symbol: "src/other.py::work",
      line_start: null,
      line_end: null,
      plan: { opportunity_id: "different-opportunity" },
      evidence: {},
      impact_delta: 0,
      effort_bucket: "M",
      blast_radius: {},
      confidence: "medium",
      source_biomarker: "io_in_loop",
      rank_score: 1,
    };
    const exact = adapter();
    exact.getRefactoringPlan = vi.fn(async () => unrelated);
    render(<PerformanceView adapter={exact} />);

    fireEvent.click(await screen.findByRole("button", { name: /Inspect 2 call sites/i }));
    expect(await screen.findByText(/returned plan no longer matches this opportunity/)).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Copy prompt for an agent" })).toBeNull();
  });

  it("falls back to bounded raw findings when an older server lacks grouping", async () => {
    const unavailable = Object.assign(new Error("Not found"), { status: 404 });
    const legacy = adapter(vi.fn(async () => Promise.reject(unavailable)));
    legacy.listFindings = vi.fn(async () => [
      {
        id: "finding-legacy",
        dimension: "performance" as const,
        biomarker_type: "io_in_loop",
        severity: "medium" as const,
        health_impact: 0.4,
        file_path: "src/legacy.py",
        function_name: null,
        line_start: 4,
        line_end: 4,
        reason: "Repeated file read.",
        details: {},
        status: "open",
      },
    ]);
    render(<PerformanceView adapter={legacy} />);

    expect(await screen.findByText("Raw performance findings")).toBeTruthy();
    expect(legacy.listFindings).toHaveBeenCalledWith({ dimension: "performance", limit: 100 });
    expect(screen.queryByText("No safe structured plan")).toBeNull();
  });
});
