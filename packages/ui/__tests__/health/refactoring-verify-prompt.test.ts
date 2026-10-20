/**
 * The agent handoff names the tests to run after the change, in the server's
 * relevance order with its reasons, or tells the agent to write one first.
 */
import { describe, expect, it } from "vitest";
import type { PerformanceOpportunity } from "@repowise-dev/types/health";
import type {
  RecommendationValidation,
  RefactoringOpportunityDetailResolved,
  RefactoringPlan,
} from "@repowise-dev/types/refactoring";

import {
  buildPerformanceOpportunityPrompt,
  buildRefactoringOpportunityPrompt,
  buildRefactoringPlanPrompt,
} from "../../src/health/ai-prompt-builder";

function validation(overrides: Partial<RecommendationValidation> = {}): RecommendationValidation {
  return {
    basis: "inferred",
    via: "call-graph",
    total: 34,
    tests: ["tests/unit/health/test_complexity_walker.py", "tests/unit/health/test_file_nloc.py"],
    truncated: true,
    affected_files: ["pkg/walker.py"],
    affected_symbols: ["walk_file"],
    commands: ["pytest tests/unit/health/test_complexity_walker.py tests/unit/health/test_file_nloc.py"],
    targets: [],
    reasons: {
      "tests/unit/health/test_complexity_walker.py": "calls walk_file from 9 test functions",
      "tests/unit/health/test_file_nloc.py": "imports walk_file",
    },
    ...overrides,
  };
}

function plan(v: RecommendationValidation | undefined): RefactoringPlan {
  return {
    id: "refac2_walk",
    refactoring_type: "extract_method",
    file_path: "pkg/walker.py",
    target_symbol: "walk_file",
    line_start: 94,
    line_end: 208,
    plan: { span: { start: 120, end: 140 }, params: [], returns: [] },
    evidence: {},
    impact_delta: 1.2,
    effort_bucket: "M",
    blast_radius: {},
    confidence: "high",
    source_biomarker: "complex_method",
    rank_score: 1,
    ...(v ? { validation: v } : {}),
  };
}

describe("Verify section", () => {
  it("lists the ranked tests with their reasons, then the command", () => {
    const prompt = buildRefactoringPlanPrompt({ plan: plan(validation()) });
    const verify = prompt.slice(prompt.indexOf("## Verify"));
    expect(verify).toContain(
      "- `tests/unit/health/test_complexity_walker.py` (calls walk_file from 9 test functions)\n" +
        "- `tests/unit/health/test_file_nloc.py` (imports walk_file)",
    );
    expect(verify).toContain("2 of 34 guarding tests shown.");
    expect(verify).toContain(
      "```\npytest tests/unit/health/test_complexity_walker.py tests/unit/health/test_file_nloc.py\n```",
    );
    // The order the server ranked is the order the agent reads.
    expect(verify.indexOf("test_complexity_walker")).toBeLessThan(verify.indexOf("test_file_nloc"));
  });

  it("tells the agent to add a test when nothing guards the symbol", () => {
    const prompt = buildRefactoringPlanPrompt({
      plan: plan(
        validation({ basis: "unknown", via: null, total: 0, tests: [], commands: [], reasons: {} }),
      ),
    });
    expect(prompt).toContain(
      "No guarding tests found: add a test for `walk_file` before changing it.",
    );
  });

  it("renders a test without a reason from an older server", () => {
    const { reasons: _dropped, ...older } = validation();
    const prompt = buildRefactoringPlanPrompt({ plan: plan(older) });
    expect(prompt).toContain("- `tests/unit/health/test_file_nloc.py`\n");
  });

  it("says nothing about tests when the payload carries no validation", () => {
    expect(buildRefactoringPlanPrompt({ plan: plan(undefined) })).not.toContain("## Verify");
  });

  it("names a profile's step symbols in the opportunity prompt", () => {
    const empty = validation({ basis: "unknown", via: null, total: 0, tests: [], commands: [] });
    const opportunity = {
      opportunity_id: "opp_1",
      file_path: "pkg/walker.py",
      lead_refactoring_type: "extract_method",
      lead_biomarker: "complex_method",
      step_count: 1,
      mechanical_steps: 1,
      judgment_steps: 0,
      recoverable_health: 1,
      effort_bucket: "M",
      confidence: "high",
      addresses_primary_problem: true,
      affected_files: ["pkg/walker.py"],
      steps: [
        {
          plan_id: "refac2_walk",
          refactoring_type: "extract_method",
          target_symbol: "walk_file",
          file_path: "pkg/walker.py",
          line_start: 94,
          line_end: 208,
          effort_bucket: "M",
          confidence: "high",
          impact_delta: 1,
          source_biomarker: "complex_method",
          relocated_by: null,
          applicability: { classification: "mechanical", reasons: [], unknowns: [] },
          validation_profile_id: "validation_a",
        },
      ],
      evidence: [],
      evidence_emitted: 0,
      evidence_truncated: false,
      validation_profiles: [{ id: "validation_a", ...empty }],
      plans: [plan(empty)],
      next_actions: [],
    } as unknown as RefactoringOpportunityDetailResolved;
    const prompt = buildRefactoringOpportunityPrompt({ opportunity });
    expect(prompt).toContain("## Verify");
    expect(prompt).toContain(
      "No guarding tests found: add a test for `walk_file` before changing it.",
    );
  });

  it("adds Verify to the performance handoff when the queue carries validation", () => {
    const opportunity = {
      opportunity_id: "perf_1",
      biomarker_type: "n_plus_one",
      execution_context: "request",
      boundary_kind: "database",
      confidence: "high",
      provenance: "call_graph",
      affected_call_sites_total: 3,
      affected_files_total: 1,
      intervention_symbol: "pkg/decision_graph.py::sync_links_from_record",
      terminal_sink: null,
      file_path: "pkg/decision_graph.py",
      plan_reason: "A stored plan exists.",
      evidence: [],
      validation: {
        basis: "inferred",
        via: "call-graph",
        total: 1,
        tests: ["tests/test_decision_graph.py"],
        reasons: { "tests/test_decision_graph.py": "calls sync_links_from_record" },
        commands: ["pytest tests/test_decision_graph.py"],
      },
    } as unknown as PerformanceOpportunity;
    const prompt = buildPerformanceOpportunityPrompt({ opportunity });
    expect(prompt).toContain(
      "- `tests/test_decision_graph.py` (calls sync_links_from_record)",
    );
    expect(prompt.indexOf("## Verify")).toBeLessThan(prompt.indexOf("## What to do"));

    const bare = buildPerformanceOpportunityPrompt({
      opportunity: {
        ...opportunity,
        validation: { ...opportunity.validation!, total: 0, tests: [], commands: [] },
      },
    });
    expect(bare).toContain(
      "No guarding tests found: add a test for `sync_links_from_record` before changing it.",
    );
  });
});
