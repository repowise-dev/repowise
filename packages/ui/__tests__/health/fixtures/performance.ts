import { vi } from "vitest";
import type {
  PerformanceOpportunity,
  PerformanceOpportunityDetail,
  PerformanceOpportunityPage,
} from "@repowise-dev/types/health";

import type { PerformanceViewAdapter } from "../../../src/health/performance/adapter";

/**
 * Synthetic queue fixtures shaped like the real response.
 *
 * Synthetic on purpose: an edge state such as a stale model, a truncated
 * evidence preview, or a context with no rows has to be reachable on demand,
 * and a live index only offers whatever it happens to hold.
 */

export function opportunity(
  overrides: Partial<PerformanceOpportunity> = {},
): PerformanceOpportunity {
  return {
    opportunity_id: "perf2_aaaaaaaaaaaaaaaaaaaa",
    performance_model_version: 2,
    biomarker_type: "io_in_loop",
    biomarker_types: ["io_in_loop"],
    boundary_kind: "db",
    execution_context: "production",
    terminal_sink: "src/db.py::fetch",
    shared_path_suffix: ["src/shared.py::load", "src/db.py::fetch"],
    intervention_symbol: "src/shared.py::load",
    file_path: "src/shared.py",
    resource_fingerprints: [],
    affected_call_sites_total: 2,
    affected_files_total: 2,
    observations_total: 6,
    evidence: [
      {
        finding_id: "finding_1111",
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
        finding_id: "finding_2222",
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
    evidence_total: 6,
    evidence_emitted: 2,
    evidence_next_cursor: 2,
    reliable_entry_reachability: true,
    provenance: "reliable-edge",
    confidence: "medium",
    facets: {
      actionability_confidence: "high",
      exposure: "entry_reachable",
      amplification: "per_iteration",
      leverage: "local",
      change_risk: "moderate",
    },
    actionability_state: "advisory",
    actionability_reason: "strategy_requires_validation",
    prerequisites: ["batch_api_contract"],
    rank_score: 12.5,
    rank_position: 1,
    rank_factors: { boundary_kind: 4 },
    why_ranked: [{ factor: "boundary_kind", value: "db", points: 4 }],
    fix: {
      strategy: "batch_or_prefetch_io",
      safety: "advisory",
      rationale: "Validate result equivalence.",
    },
    plan_id: "plan-1",
    plan_status: "available",
    plan_reason: "A stored performance plan addresses this exact opportunity.",
    ...overrides,
  };
}

/** The five facet groups the server counts, with plausible totals. */
export function facets(): PerformanceOpportunityPage["facets"] {
  return {
    context: [
      { value: "production", total: 4 },
      { value: "test", total: 2 },
      { value: "tooling", total: 1 },
    ],
    boundary: [
      { value: "db", total: 4 },
      { value: "filesystem", total: 2 },
      { value: "none", total: 1 },
    ],
    confidence: [{ value: "high", total: 7 }],
    actionability: [
      { value: "investigate", total: 4 },
      { value: "advisory", total: 2 },
      { value: "plan_ready", total: 1 },
    ],
    plan_state: [
      { value: "no_safe_plan", total: 4 },
      { value: "available", total: 3 },
    ],
  };
}

export function page(overrides: Partial<PerformanceOpportunityPage> = {}): PerformanceOpportunityPage {
  return {
    items: [
      opportunity({
        opportunity_id: "perf2_planready",
        actionability_state: "plan_ready",
        actionability_reason: "proven_strategy",
        rank_position: 1,
        confidence: "high",
        fix: {
          strategy: "parallelize_independent_awaits",
          safety: "proven",
          rationale: "Dataflow proves the iterations carry no dependence.",
        },
      }),
      opportunity({
        opportunity_id: "perf2_advisory",
        actionability_state: "advisory",
        rank_position: 2,
      }),
      opportunity({
        opportunity_id: "perf2_investigate",
        actionability_state: "investigate",
        rank_position: 3,
        boundary_kind: null,
        terminal_sink: null,
        intervention_symbol: null,
        shared_path_suffix: [],
        biomarker_type: "membership_test_against_list_in_loop",
        biomarker_types: ["membership_test_against_list_in_loop"],
        fix: null,
        plan_id: null,
        plan_status: "no_safe_plan",
        plan_reason: "No coherent intervention was proven.",
      }),
    ],
    total: 7,
    has_more: true,
    next_offset: 3,
    facets: facets(),
    summary: {
      status: "current",
      total: 7,
      repository_total: 7,
      performance_model_version: 2,
      analyzed_commit: "848a8f180abc",
      actionability: { plan_ready: 1, advisory: 2, investigate: 4 },
      context: { production: 4, test: 2, tooling: 1 },
      boundary: { db: 4, filesystem: 2 },
      with_plan_total: 3,
    },
    ...overrides,
  };
}

/** A server that predates canonical contexts and server-owned facets. */
export function legacyPage(): PerformanceOpportunityPage {
  const base = page();
  return {
    ...base,
    facets: {},
    summary: {
      total: 7,
      with_plan_total: 3,
    } as PerformanceOpportunityPage["summary"],
  };
}

export function resolvedDetail(
  overrides: Partial<Extract<PerformanceOpportunityDetail, { resolved: true }>> = {},
): PerformanceOpportunityDetail {
  return {
    ...opportunity({ opportunity_id: "perf2_planready" }),
    resolved: true,
    lifecycle_status: "open",
    analyzed_commit: "848a8f180abc",
    model_state: {
      state: "current",
      opportunity_id: "perf2_planready",
      requested_model_version: 2,
      performance_model_version: 2,
      refresh_required: false,
    },
    evidence_total: 6,
    evidence_emitted: 2,
    ...overrides,
  };
}

/** `undefined` is a meaningful override here: it is how a host declines one. */
type AdapterOverrides = {
  [K in keyof PerformanceViewAdapter]?: PerformanceViewAdapter[K] | undefined;
};

export function adapter(overrides: AdapterOverrides = {}): PerformanceViewAdapter {
  return {
    cacheKey: `repo-${Math.random()}`,
    listFindings: vi.fn(async () => []),
    getPerformanceOpportunities: vi.fn(async () => page()),
    getPerformanceOpportunityFindings: vi.fn(async () => ({
      items: [],
      total: 0,
      has_more: false,
      next_offset: null,
    })),
    refactoringPlanHref: (planId: string) => `/refactoring?plan=${planId}`,
    fileHref: (path: string) => `/files/${path}`,
    symbolHref: (symbol: string) => `/symbols/${symbol}`,
    navigate: vi.fn(),
    ...overrides,
  } as PerformanceViewAdapter;
}
