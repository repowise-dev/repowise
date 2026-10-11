import type { PerformanceOpportunity } from "@repowise-dev/types/health";
import type {
  OpportunityStep,
  RecommendationValidation,
  RefactoringOpportunityDetailResolved,
  RefactoringPlan,
} from "@repowise-dev/types/refactoring";

import { typeMeta } from "../../refactoring/meta";
import { blastFiles } from "../../refactoring/types";
import {
  planSourceLink,
  refactoringPlanSteps,
  verifySubject,
} from "./refactoring-plan-steps";
import {
  bulletList,
  closingSections,
  explorationCloser,
  FLAVOR_PREAMBLE,
  joinSections,
  pluralS,
  repoSuffix,
  verifyLines,
  verifySection,
  type AiPromptFlavor,
} from "./shared";

// ─────────────────────────────────────────────────────────────────────
// Refactoring plan prompt (hand a deterministic plan to a coding agent)
// ─────────────────────────────────────────────────────────────────────

export interface BuildPerformanceOpportunityPromptOptions {
  opportunity: PerformanceOpportunity;
  flavor?: AiPromptFlavor;
}

/** An evidence-first handoff used only when no exact persisted plan is available. */
export function buildPerformanceOpportunityPrompt({
  opportunity,
  flavor = "generic",
}: BuildPerformanceOpportunityPromptOptions): string {
  const evidence = opportunity.evidence.map((item) => {
    const location = `${item.file_path}${item.line_start ? `:${item.line_start}` : ""}`;
    const path = item.path.length ? `; path: ${item.path.join(" -> ")}` : "";
    return `- \`${location}\`: ${item.reason} (${item.provenance}${path})`;
  });
  const intervention = opportunity.intervention_symbol
    ? `Candidate shared intervention: \`${opportunity.intervention_symbol}\`.`
    : "No shared intervention was proven.";
  const subject = (
    opportunity.intervention_symbol ??
    opportunity.terminal_sink ??
    opportunity.file_path
  )
    .split("::")
    .pop() ?? opportunity.file_path;
  // An older payload carries no validation; it keeps no Verify block.
  const verify = opportunity.validation ? verifySection(opportunity.validation, [subject]) : "";

  return [
    FLAVOR_PREAMBLE[flavor],
    "",
    "## Causal performance opportunity",
    "",
    `- Stable opportunity ID: \`${opportunity.opportunity_id}\`.`,
    `- Detector: \`${opportunity.biomarker_type}\`.`,
    `- Execution context: ${opportunity.execution_context}.`,
    `- Boundary: ${opportunity.boundary_kind ?? "unknown"}.`,
    `- Confidence: ${opportunity.confidence}; provenance: ${opportunity.provenance}.`,
    `- Scope: ${opportunity.affected_call_sites_total} affected call sites across ${opportunity.affected_files_total} files.`,
    `- ${intervention}`,
    `- Product status: ${opportunity.plan_reason}`,
    "",
    "## Evidence sampled by the analyzer",
    "",
    evidence.length ? evidence.join("\n") : "No resolved evidence paths were included in this page.",
    "",
    ...(verify ? [verify, ""] : []),
    "## What to do",
    "",
    "1. Verify the repeated cost and caller-to-sink paths against the real code.",
    "2. Determine whether one behavior-preserving intervention safely addresses the shared cause.",
    verify
      ? "3. Use the tests under Verify to prove result equivalence, and add a measurement that shows the performance improvement."
      : "3. Identify the tests and commands that prove result equivalence and performance improvement.",
    "4. If the evidence is insufficient or the intervention is unsafe, stop and explain the blocker instead of editing.",
    "",
    "Do not run commands or change files until the evidence and proposed validation have been reviewed.",
  ].join("\n");
}

export interface BuildRefactoringPlanPromptOptions {
  plan: RefactoringPlan;
  flavor?: AiPromptFlavor;
  repoName?: string;
}

/** Each item in backticks, joined. */
function codeList(items: string[], separator = ", "): string {
  return items.map((item) => `\`${item}\``).join(separator);
}

/** How many tests guard the change and how that was established. */
function guardingTests(v: RecommendationValidation): string | null {
  // With none listed, the Verify block already says to add one.
  if (v.basis === "unknown" || v.tests.length === 0) return null;
  return `${v.total} guarding test${pluralS(v.total)} via ${v.via ?? v.basis}.`;
}

function recommendationValidation(plan: RefactoringPlan): string {
  const validation = plan.validation;
  if (!validation) return "";
  return [
    verifySection(validation, [verifySubject(plan)]),
    "",
    bulletList([
      guardingTests(validation),
      validation.affected_files.length
        ? `Affected files: ${codeList(validation.affected_files)}.`
        : null,
      validation.affected_symbols.length
        ? `Affected symbols: ${codeList(validation.affected_symbols)}.`
        : null,
    ]),
  ].join("\n");
}

const PLAN_EXPECTED = [
  "1. The refactored code, with each step above applied.",
  "2. A short note on what you renamed/introduced and why.",
  "3. Confirmation the tests pass (or the exact failures if they don't).",
  "4. Any call sites or co-changed files you had to update.",
];

/** Where the plan points, what it is, and what it is worth. */
function planFacts(plan: RefactoringPlan, blurb: string): string {
  return bulletList([
    `Target: ${planSourceLink(plan.file_path, plan.line_start, plan.line_end)}${
      plan.target_symbol ? ` — \`${plan.target_symbol}\`` : ""
    }`,
    `What: ${blurb}`,
    plan.impact_delta > 0
      ? `Recovers ~${plan.impact_delta.toFixed(2)} of health score if applied.`
      : null,
    plan.effort_bucket ? `Effort: ${plan.effort_bucket} bucket.` : null,
    plan.confidence ? `Detector confidence: ${plan.confidence}.` : null,
  ]);
}

/**
 * Hand an agent ONE precomputed refactoring plan to execute and verify, with
 * its per-type steps, blast radius and completion contract.
 *
 * Fallback only: the server renders this prompt in core
 * (`/refactoring/{id}/prompt`). Kept for a host talking to a server older than
 * that route (the VS Code extension supports servers from 0.47); remove with
 * the opportunity builder below once no supported server lacks it.
 */
export function buildRefactoringPlanPrompt({
  plan,
  flavor = "generic",
  repoName,
}: BuildRefactoringPlanPromptOptions): string {
  const meta = typeMeta(plan.refactoring_type);
  const files = blastFiles(plan).filter((f) => f !== plan.file_path);

  const constraintList = [
    "Preserve behavior exactly — this is a refactoring, not a feature change. No public API or observable behavior should shift.",
    "Run the project's tests (and type-checker/linter) after the change; the suite must stay green.",
    "If, after reading the real code, the plan looks wrong or unsafe, stop and explain why instead of forcing it — the detection is static and can be a false positive.",
    files.length > 0 ? `Keep these co-affected files consistent: ${codeList(files)}.` : null,
  ];

  return joinSections([
    FLAVOR_PREAMBLE[flavor],
    "",
    `## ${meta.label}${repoSuffix(repoName)}`,
    "",
    planFacts(plan, meta.blurb),
    "",
    "## The plan",
    "",
    refactoringPlanSteps(plan),
    "",
    recommendationValidation(plan),
    "",
    ...closingSections(constraintList, PLAN_EXPECTED),
    explorationCloser(flavor, plan.file_path, "refactor"),
  ]);
}

// ─────────────────────────────────────────────────────────────────────
// Refactoring opportunity prompt (the ordered steps for one file)
// ─────────────────────────────────────────────────────────────────────

export interface BuildRefactoringOpportunityPromptOptions {
  opportunity: RefactoringOpportunityDetailResolved;
  flavor?: AiPromptFlavor;
  repoName?: string;
}

/** `- Mechanical (dataflow proved the extraction).` */
function stepApplicabilityLine(step: OpportunityStep): string {
  const classification =
    step.applicability.classification === "mechanical" ? "Mechanical" : "Judgment";
  const reasons = step.applicability.reasons.map((r) => r.replace(/_/g, " ")).join("; ");
  const unknowns = step.applicability.unknowns.length
    ? ` Not established: ${step.applicability.unknowns
        .map((u) => u.replace(/_/g, " "))
        .join(", ")}.`
    : "";
  return `${classification}${reasons ? ` — ${reasons}` : ""}.${unknowns}`;
}

/** Indent a multi-line block so it reads as the body of its numbered step. */
function indentBlock(body: string, pad: string): string {
  return body
    .split("\n")
    .map((line) => (line.trim() === "" ? "" : `${pad}${line}`))
    .join("\n");
}

function stepEntry(step: OpportunityStep, index: number, plan: RefactoringPlan | undefined): string {
  const meta = typeMeta(step.refactoring_type);
  const lines: (string | null)[] = [
    `${index + 1}. **${meta.label}** — ${planSourceLink(step.file_path, step.line_start, step.line_end)}${
      step.target_symbol ? ` — \`${step.target_symbol}\`` : ""
    }`,
    `   - Step id: \`${step.plan_id}\``,
    `   - ${stepApplicabilityLine(step)}`,
    step.relocated_by
      ? `   - **Locate it again first.** Step \`${step.relocated_by}\` moves this symbol to another file, so the path and lines above are where it was, not where it will be when you get here.`
      : null,
    plan
      ? indentBlock(refactoringPlanSteps(plan), "   ")
      : // Not a silent gap: a step whose payload did not come back still
        // says so rather than rendering a header with no instruction.
        `   - The detail for this step was not in this payload. Ask for it by id: \`${step.plan_id}\`.`,
  ];
  return lines.filter(Boolean).join("\n");
}

/**
 * The ordered steps. A `relocated_by` step gets its own line because an earlier
 * step moves its symbol, so its recorded path and span go stale mid-run.
 */
function opportunitySteps(opportunity: RefactoringOpportunityDetailResolved): string {
  const byId = new Map(opportunity.plans.map((plan) => [plan.id, plan]));
  return opportunity.steps
    .map((step, i) => stepEntry(step, i, byId.get(step.plan_id)))
    .join("\n\n");
}

/** The observations behind the diagnosis, never presented as work. */
function opportunityEvidence(opportunity: RefactoringOpportunityDetailResolved): string {
  if (opportunity.evidence.length === 0) return "";
  const rows = opportunity.evidence.map((item) => {
    const meta = typeMeta(item.refactoring_type);
    const detail = Object.entries(item.summary)
      .filter(([, value]) => value !== null && value !== undefined && value !== "")
      .map(([key, value]) => `${key.replace(/_/g, " ")}: ${String(value)}`)
      .join("; ");
    return `- ${meta.label}${item.target_symbol ? ` \`${item.target_symbol}\`` : ""}${
      detail ? ` — ${detail}` : ""
    }`;
  });
  return joinSections([
    "## Evidence behind the diagnosis",
    "",
    "Supporting observations, not extra work. They are why the diagnosis reads the way it does.",
    "",
    rows.join("\n"),
    opportunity.evidence_truncated
      ? `\n${opportunity.evidence_emitted} of ${opportunity.evidence_total} shown.`
      : "",
  ]);
}

/** What each validation profile's steps change, so a missing test names it. */
function profileSubjects(opportunity: RefactoringOpportunityDetailResolved, id: string): string[] {
  const plans = new Map(opportunity.plans.map((plan) => [plan.id, plan]));
  const subjects = opportunity.steps
    .filter((step) => step.validation_profile_id === id)
    .map((step) => {
      const plan = plans.get(step.plan_id);
      return plan ? verifySubject(plan) : step.target_symbol || step.file_path;
    });
  return subjects.length ? [...new Set(subjects)] : [opportunity.file_path];
}

/** The validation profiles the steps share: ranked tests with reasons and commands. */
function opportunityValidation(opportunity: RefactoringOpportunityDetailResolved): string {
  const profiles = opportunity.validation_profiles;
  if (profiles.length === 0) return "";
  const blocks = profiles.map((profile) => {
    const subjects = profileSubjects(opportunity, profile.id);
    const summary = guardingTests(profile);
    return [
      ...(profiles.length > 1 ? [`### For ${codeList(subjects)}`, ""] : []),
      ...(summary ? [summary, ""] : []),
      ...verifyLines(profile, subjects),
    ].join("\n");
  });
  return ["## Verify", "", blocks.join("\n\n")].join("\n");
}

/**
 * The opportunity id, stated for every flavor so work can be reported against
 * it. Only the MCP flavor is told a call that resolves it; the others are told
 * everything is inlined because they have no such tool.
 */
function opportunityHandoff(
  opportunity: RefactoringOpportunityDetailResolved,
  flavor: AiPromptFlavor,
): string {
  if (flavor === "claude-code-mcp") {
    return [
      "## Pull this record yourself",
      "",
      `\`get_health(opportunity_id="${opportunity.opportunity_id}")\``,
      "",
      "That returns this same opportunity from the index: every ordered step with its mechanical/judgment classification and reasons, the member plans' payloads, the validation profiles with runnable commands, the evidence, and structured next actions. Call it before you start — the steps below are a snapshot, and the index is the record.",
    ].join("\n");
  }
  return [
    "## This opportunity's id",
    "",
    `\`${opportunity.opportunity_id}\``,
    "",
    "Quote it when you report back. You have no tool here that resolves it, so everything needed to execute the work is inlined above rather than left behind a call you cannot make.",
  ].join("\n");
}

// Tri-state, and all three states survive. `null` means no dominant finding
// was recorded to compare against, which is not the claim `false` makes.
function primaryProblemLine(addressesPrimary: boolean | null | undefined): string {
  if (addressesPrimary === true) {
    return "These steps address the file's dominant diagnosed problem.";
  }
  if (addressesPrimary === false) {
    return "These steps do NOT address the file's dominant diagnosed problem — they are real work, but not the biggest cost in this file. Say so if you think something else should come first.";
  }
  return "No dominant problem was recorded for this file, so whether these steps address the main one is unknown — not answered either way.";
}

/** What the opportunity is, how big it is, and whether it hits the main problem. */
function opportunityFacts(opportunity: RefactoringOpportunityDetailResolved): string {
  return bulletList([
    `Opportunity: \`${opportunity.opportunity_id}\``,
    `File: \`${opportunity.file_path}\``,
    opportunity.lead_biomarker
      ? `Leading cause: ${opportunity.lead_biomarker.replace(/_/g, " ")}.`
      : null,
    `${opportunity.step_count} step${pluralS(opportunity.step_count)}: ${opportunity.mechanical_steps} mechanical, ${opportunity.judgment_steps} judgment.`,
    opportunity.recoverable_health > 0
      ? `Recovers ~${opportunity.recoverable_health.toFixed(2)} of health score if applied.`
      : null,
    `Effort: ${opportunity.effort_bucket} bucket. Detector confidence: ${opportunity.confidence}.`,
    primaryProblemLine(opportunity.addresses_primary_problem),
  ]);
}

/**
 * Hand an agent ONE composed refactoring opportunity: its ordered steps with the
 * mechanical/judgment split, evidence, validation and stable id. The multi-step
 * sibling of `buildRefactoringPlanPrompt`, and a fallback the same way: the
 * server renders it at `/refactoring/opportunities/{id}/prompt`.
 */
export function buildRefactoringOpportunityPrompt({
  opportunity,
  flavor = "generic",
  repoName,
}: BuildRefactoringOpportunityPromptOptions): string {
  const meta = typeMeta(opportunity.lead_refactoring_type || "");
  const others = opportunity.affected_files.filter((f) => f !== opportunity.file_path);
  const anyRelocated = opportunity.steps.some((step) => Boolean(step.relocated_by));

  const constraintList = [
    "Preserve behavior exactly — this is a refactoring, not a feature change. No public API or observable behavior should shift.",
    "Apply the steps in the order given. It is dependency-safe; reordering it is not.",
    anyRelocated
      ? "Where a step says to locate its symbol again, do that before touching it: an earlier step moves it, so the recorded path and lines go stale mid-run."
      : null,
    "A step marked Judgment has an unproven obligation. Read the real code and decide before applying it; do not treat it like a mechanical one.",
    "Run the project's tests (and type-checker/linter) after the change; the suite must stay green.",
    "If, after reading the real code, a step looks wrong or unsafe, stop and explain why instead of forcing it — the detection is static and can be a false positive.",
    others.length > 0 ? `Keep these co-affected files consistent: ${codeList(others)}.` : null,
  ];

  const completionContract = [
    "1. The refactored code, with each step above applied in order.",
    "2. Per step: whether you applied it, and for anything you skipped, the reason.",
    "3. A short note on what you renamed or introduced and why.",
    "4. Confirmation the tests pass (or the exact failures if they don't).",
    `5. The opportunity id \`${opportunity.opportunity_id}\`, so the work can be matched back to the record.`,
  ];

  return joinSections([
    FLAVOR_PREAMBLE[flavor],
    "",
    `## ${meta.label} in \`${opportunity.file_path}\`${repoSuffix(repoName)}`,
    "",
    opportunityFacts(opportunity),
    "",
    "## The steps, in order",
    "",
    anyRelocated
      ? `${opportunity.ordering_note ?? "One or more steps below are moved by an earlier step; each says so on its own line."}\n`
      : "",
    opportunitySteps(opportunity),
    "",
    opportunityEvidence(opportunity),
    "",
    opportunityValidation(opportunity),
    "",
    ...closingSections(constraintList, completionContract),
    opportunityHandoff(opportunity, flavor),
    "",
    explorationCloser(flavor, opportunity.file_path, "refactor"),
  ]);
}
