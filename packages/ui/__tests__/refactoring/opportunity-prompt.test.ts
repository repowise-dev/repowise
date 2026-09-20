/**
 * What the pasted refactoring prompt has to carry.
 *
 * The plan-level prompt it sits beside embeds the steps and nothing that
 * identifies the record, so an agent could execute it and had no way to ask for
 * it, report against it, or notice it had gone stale. These pin the four things
 * that were missing: the id, the call that resolves the id, the evidence, and
 * the mechanical/judgment split — plus the ordering hazard, which is the one
 * that makes a correct-looking step edit the wrong file.
 */

import { describe, expect, it } from "vitest";
import type {
  RefactoringOpportunityDetailResolved,
  RefactoringPlan,
} from "@repowise-dev/types/refactoring";

import { buildRefactoringOpportunityPrompt } from "../../src/health/ai-prompt-builder";

function plan(overrides: Partial<RefactoringPlan> = {}): RefactoringPlan {
  return {
    id: "refac2_split",
    refactoring_type: "split_file",
    file_path: "src/big.py",
    target_symbol: "big.py",
    line_start: 1,
    line_end: 900,
    plan: {
      groups: [{ suggested_file: "big_io.py", symbols: ["read", "write"] }],
      shim_required: true,
    },
    evidence: { symbol_count: 40, file_nloc: 900, modularity: 0.61 },
    impact_delta: 1.2,
    effort_bucket: "L",
    blast_radius: { files: ["src/other.py"], file_count: 1 },
    confidence: "high",
    source_biomarker: "long_file",
    rank_score: 1.45,
    ...overrides,
  };
}

function detail(
  overrides: Partial<RefactoringOpportunityDetailResolved> = {},
): RefactoringOpportunityDetailResolved {
  return {
    resolved: true,
    opportunity_id: "refop2_abc123",
    refactoring_model_version: 2,
    status: "open",
    file_path: "src/big.py",
    lead_biomarker: "long_file",
    lead_refactoring_type: "split_file",
    addresses_primary_problem: true,
    effort_bucket: "L",
    confidence: "high",
    step_count: 2,
    mechanical_steps: 1,
    judgment_steps: 1,
    evidence_total: 1,
    affected_files_total: 2,
    recoverable_health: 1.4,
    rank_score: 1.45,
    rank_position: 1,
    queue_position: 1,
    rank_factors: {},
    why_ranked: [],
    steps: [
      {
        plan_id: "refac2_split",
        refactoring_type: "split_file",
        target_symbol: "big.py",
        file_path: "src/big.py",
        line_start: 1,
        line_end: 900,
        effort_bucket: "L",
        confidence: "high",
        impact_delta: 1.2,
        source_biomarker: "long_file",
        relocated_by: null,
        applicability: {
          classification: "judgment",
          reasons: ["changes_symbol_home"],
          facts: {},
          unknowns: ["framework_registration"],
        },
      },
      {
        plan_id: "refac2_extract",
        refactoring_type: "extract_method",
        target_symbol: "big.py::render",
        file_path: "src/big.py",
        line_start: 400,
        line_end: 460,
        effort_bucket: "S",
        confidence: "high",
        impact_delta: 0.4,
        source_biomarker: "complex_method",
        relocated_by: "refac2_split",
        applicability: {
          classification: "mechanical",
          reasons: ["dataflow_proved_extraction"],
          facts: {},
          unknowns: [],
        },
      },
    ],
    steps_total: 2,
    steps_emitted: 2,
    evidence: [
      {
        plan_id: "refac2_clone",
        refactoring_type: "extract_helper",
        target_symbol: "big.py:10-25",
        source_biomarker: "dry_violation",
        summary: { duplicated_lines: 16, occurrence_count: 2 },
      },
    ],
    evidence_emitted: 1,
    evidence_truncated: false,
    affected_files: ["src/big.py", "src/other.py"],
    validation_profiles: [
      {
        id: "validation_1",
        basis: "measured",
        via: "coverage",
        total: 3,
        tests: ["tests/test_big.py"],
        truncated: false,
        affected_files: ["src/big.py"],
        affected_symbols: [],
        commands: ["pytest tests/test_big.py"],
        targets: [],
      },
    ],
    plans: [plan()],
    next_actions: [],
    ordering_note: "Step 2 is relocated by step 1.",
    ...overrides,
  };
}

describe("buildRefactoringOpportunityPrompt", () => {
  it("carries the opportunity id so the work can be reported back", () => {
    const text = buildRefactoringOpportunityPrompt({ opportunity: detail() });
    expect(text).toContain("refop2_abc123");
    // And in the completion contract, not only as a header fact.
    expect(text).toMatch(/opportunity id `refop2_abc123`/);
  });

  it("names the call that resolves the id, but only for a flavor that can make it", () => {
    const mcp = buildRefactoringOpportunityPrompt({
      opportunity: detail(),
      flavor: "claude-code-mcp",
    });
    expect(mcp).toContain('get_health(opportunity_id="refop2_abc123")');

    // An id with no tool that accepts it is noise; the non-MCP flavors say so
    // rather than naming a call they cannot invoke.
    for (const flavor of ["generic", "claude-code", "cursor"] as const) {
      const text = buildRefactoringOpportunityPrompt({ opportunity: detail(), flavor });
      expect(text).toContain("refop2_abc123");
      expect(text).not.toContain("get_health(opportunity_id=");
      expect(text).toMatch(/no tool here that resolves it/i);
    }
  });

  it("marks every step mechanical or judgment, with its reason", () => {
    const text = buildRefactoringOpportunityPrompt({ opportunity: detail() });
    expect(text).toMatch(/Judgment — changes symbol home/);
    expect(text).toMatch(/Mechanical — dataflow proved extraction/);
    // An unknown is first-class: it must not read as a check that passed.
    expect(text).toMatch(/Not established: framework registration/);
  });

  it("warns that a relocated step's own coordinates go stale", () => {
    const text = buildRefactoringOpportunityPrompt({ opportunity: detail() });
    expect(text).toContain("Locate it again first");
    expect(text).toContain("refac2_split");
    expect(text).toMatch(/where it was, not where it will be/);
  });

  it("does not warn about ordering when no step is relocated", () => {
    const steps = detail().steps.map((s) => ({ ...s, relocated_by: null }));
    // Omitted, not set to undefined: the wire type is exact-optional, and the
    // server omits the key rather than emitting a null when nothing relocates.
    const { ordering_note: _omitted, ...rest } = detail();
    const text = buildRefactoringOpportunityPrompt({
      opportunity: { ...rest, steps },
    });
    expect(text).not.toContain("Locate it again first");
  });

  it("renders the evidence as observation, never as work", () => {
    const text = buildRefactoringOpportunityPrompt({ opportunity: detail() });
    expect(text).toContain("## Evidence behind the diagnosis");
    expect(text).toMatch(/Supporting observations, not extra work/);
    expect(text).toContain("Extract Helper");
  });

  it("preserves all three states of addresses_primary_problem", () => {
    const yes = buildRefactoringOpportunityPrompt({
      opportunity: detail({ addresses_primary_problem: true }),
    });
    expect(yes).toMatch(/address the file's dominant diagnosed problem/);
    expect(yes).not.toMatch(/do NOT address/);

    const no = buildRefactoringOpportunityPrompt({
      opportunity: detail({ addresses_primary_problem: false }),
    });
    expect(no).toMatch(/do NOT address the file's dominant diagnosed problem/);

    // `null` is not `false`. Reading one as the other turns "we could not tell"
    // into an accusation about the plan.
    const unknown = buildRefactoringOpportunityPrompt({
      opportunity: detail({ addresses_primary_problem: null }),
    });
    expect(unknown).toMatch(/No dominant problem was recorded/);
    expect(unknown).toMatch(/not answered either way/);
    expect(unknown).not.toMatch(/do NOT address/);
  });

  it("carries the runnable validation commands", () => {
    const text = buildRefactoringOpportunityPrompt({ opportunity: detail() });
    expect(text).toContain("## Validation plan");
    expect(text).toContain("pytest tests/test_big.py");
    expect(text).toMatch(/3 guarding tests via coverage/);
  });

  it("names the validation gap rather than staying silent about it", () => {
    const profiles = [
      { ...detail().validation_profiles[0]!, basis: "unknown" as const, total: 0, tests: [] },
    ];
    const text = buildRefactoringOpportunityPrompt({
      opportunity: detail({ validation_profiles: profiles }),
    });
    expect(text).toMatch(/treat this as a validation gap/);
  });

  it("tells the agent to keep the co-affected files consistent", () => {
    const text = buildRefactoringOpportunityPrompt({ opportunity: detail() });
    expect(text).toContain("src/other.py");
    expect(text).not.toMatch(/Keep these co-affected files consistent:.*src\/big\.py/);
  });
});
