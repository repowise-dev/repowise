import type { PerformanceOpportunity } from "@repowise-dev/types/health";

import { biomarkerInfo, CATEGORY_LABEL, splitByOrigin } from "../biomarker-glossary";
import { findingEntries, historyContextBlock, rankByImpact, remainderRollup } from "./findings";
import {
  bulletList,
  explorationCloser,
  FLAVOR_PREAMBLE,
  joinSections,
  pluralS,
  repoSuffix,
  type AiPromptFlavor,
} from "./shared";

// ─────────────────────────────────────────────────────────────────────
// File health prompt
// ─────────────────────────────────────────────────────────────────────

/**
 * Everything the file drawer knows about one file, in the shape the prompt
 * needs it.
 *
 * Structural rather than imported from the drawer, like every other input in
 * this module: the drawer imports the builder, so importing the drawer's types
 * back would be a cycle, and a host holding the same facts from somewhere else
 * can still build the prompt.
 */
export interface FileHealthPromptInput {
  file_path: string;
  score: number;
  nloc?: number | null;
  module?: string | null;
  defect_score?: number | null;
  maintainability_score?: number | null;
  performance_score?: number | null;
  line_coverage_pct?: number | null;
  has_test_file?: boolean;
  duplication_pct?: number | null;
  max_ccn?: number | null;
  primary_biomarker?: string | null;
  primary_reason?: string | null;
  total_deduction?: number | null;
}

export interface FileHealthPromptFinding {
  id: string;
  biomarker_type: string;
  severity: string;
  function_name: string | null;
  line_start?: number | null;
  line_end?: number | null;
  health_impact: number;
  reason: string;
  // Spelled with an explicit `undefined` because the drawer forwards rows
  // whose optional fields are genuinely absent, and the package compiles with
  // exactOptionalPropertyTypes.
  status?: string | undefined;
  details?: Record<string, unknown> | null | undefined;
}

export interface FileHealthPromptCategory {
  category: string;
  cap?: number | null;
  applied_deduction: number;
  capped: boolean;
  finding_count: number;
}

/** The process and topology facts the drawer's signals panel shows. */
export interface FileHealthPromptSignals {
  commit_count_90d?: number | null;
  change_entropy_pct?: number | null;
  prior_defect_count?: number | null;
  bug_magnet?: boolean | null;
  last_fix_at?: string | null;
  in_degree?: number | null;
  out_degree?: number | null;
  age_days?: number | null;
}

export interface BuildFileHealthPromptOptions {
  file: FileHealthPromptInput;
  findings?: FileHealthPromptFinding[];
  categories?: FileHealthPromptCategory[];
  signals?: FileHealthPromptSignals | null;
  /** Open performance causes, when the host fetched them. */
  performance?: { items: PerformanceOpportunity[]; total: number } | null;
  /** Score movement between the last two snapshots, when known. */
  trendDelta?: number | null;
  suggestions?: Record<string, string>;
  flavor?: AiPromptFlavor;
  repoName?: string;
}

/** Spelled out in full; the rest roll up, so a noisy file stays affordable. */
const MAX_FILE_HEALTH_FINDINGS = 10;
/** Causes listed by name. Past this, the queue is where to read them. */
const MAX_FILE_HEALTH_CAUSES = 6;

const CONSTRAINTS = [
  "**Read first, edit second.** Read the file, its callers, its tests, and any obvious helpers before proposing a change.",
  "Group the findings by root cause before you start. Several markers on one function are usually one problem, and fixing them separately produces churn without improving the file.",
  "Preserve runtime behaviour. No new features, and no opportunistic rewrites in unrelated regions.",
  "Do **not** change public signatures or exported names unless a verified finding requires it. Call it out explicitly if you must.",
  "Keep test coverage at least as high as before. If you change logic, add or update tests.",
  "Match the existing style of the file and its neighbours.",
  "If a finding turns out to be a false positive once you have read the code, skip it and say why. That is a useful answer, not a failure.",
];

const EXPECTED = [
  "1. A short triage: which findings share a root cause, which are independent, and which you believe are false positives.",
  "2. A plan of 3 to 6 bullets for the ones worth acting on, ordered so the riskiest change is not the first.",
  "3. The edits, scoped to this file plus its tests and any tightly coupled helper.",
  "4. A summary of what changed, and which specific findings each change should clear.",
];

/**
 * A titled section that carries its own leading blank line, or "" when it has
 * no body. The blank line has to live inside the string because `joinSections`
 * drops bare "" separators along with the absent sections.
 */
function section(heading: string, body: string | null | undefined): string {
  return body ? `\n${heading}\n\n${body}` : "";
}

/**
 * The findings still worth work: the detailed ones, the rolled-up tail, and
 * the history markers that go to context instead.
 */
function openFindings(findings: FileHealthPromptFinding[]) {
  // Triaged findings stay on the row. A prompt that asked an agent to fix
  // something already marked resolved would be reporting the drawer's state
  // rather than the file's.
  const open = findings.filter(
    (f) => f.status !== "resolved" && f.status !== "false_positive",
  );
  // Split before ranking: history markers are scored but cannot be fixed from
  // this file, so they go to context rather than into a list of open work.
  const { codeShape, history } = splitByOrigin(open);
  const ranked = rankByImpact(codeShape);
  return {
    detailed: ranked.slice(0, MAX_FILE_HEALTH_FINDINGS),
    remainder: remainderRollup(
      ranked.slice(MAX_FILE_HEALTH_FINDINGS),
      "Handle these only after the ranked items above.",
    ),
    history,
  };
}

function scoredDimensions(file: FileHealthPromptInput): string {
  return bulletList([
    file.defect_score != null ? `Code health: **${file.defect_score.toFixed(1)}/10**` : null,
    file.maintainability_score != null
      ? `Maintainability: **${file.maintainability_score.toFixed(1)}/10**`
      : null,
    file.performance_score != null
      ? `Performance: **${file.performance_score.toFixed(1)}/10**`
      : null,
  ]);
}

function trendLine(trendDelta: number | null | undefined): string | null {
  if (trendDelta == null || trendDelta === 0) return null;
  return `Score moved ${trendDelta > 0 ? "up" : "down"} ${Math.abs(trendDelta).toFixed(2)} since the previous snapshot`;
}

function healthSnapshot(
  file: FileHealthPromptInput,
  categoryCount: number,
  trendDelta: number | null | undefined,
): string {
  return bulletList([
    `Health score: **${file.score.toFixed(1)}/10** (lower is worse; 10.0 is clean)`,
    file.total_deduction != null
      ? `Total deduction: **−${file.total_deduction.toFixed(2)}** across ${categoryCount} scoring ${
          categoryCount === 1 ? "category" : "categories"
        }`
      : null,
    trendLine(trendDelta),
    file.nloc != null ? `Size: ${file.nloc} NLOC` : null,
    file.module ? `Module: \`${file.module}\`` : null,
    file.max_ccn != null ? `Highest cyclomatic complexity in the file: ${file.max_ccn}` : null,
    file.duplication_pct != null ? `Duplication: ${file.duplication_pct.toFixed(1)}%` : null,
    file.line_coverage_pct != null
      ? `Line coverage: ${Math.round(file.line_coverage_pct)}%`
      : "Line coverage: not reported",
    file.has_test_file === false
      ? "No test file was found for this file, so treat any behaviour change as unverified until you add one."
      : null,
  ]);
}

function leadingCause(file: FileHealthPromptInput): string | null {
  if (!file.primary_biomarker) return null;
  const reason = file.primary_reason ? ` ${file.primary_reason}` : "";
  return `**${biomarkerInfo(file.primary_biomarker).label}.**${reason}`;
}

function categoryLine(c: FileHealthPromptCategory): string {
  const label = CATEGORY_LABEL[c.category as keyof typeof CATEGORY_LABEL] ?? c.category;
  const cap = c.cap != null ? `, capped at −${c.cap.toFixed(1)}` : "";
  // A capped category understates itself: the raw deductions ran past the
  // ceiling, so this figure is a floor on how bad it is, not a measurement
  // of it.
  const note = c.capped ? " — **at its ceiling**, so this understates the category" : "";
  return `${label}: −${c.applied_deduction.toFixed(2)} from ${c.finding_count} finding${pluralS(
    c.finding_count,
  )}${cap}${note}`;
}

/** Where the deduction comes from, largest category first. */
function categoryBreakdown(categories: FileHealthPromptCategory[]): string | null {
  if (categories.length === 0) return null;
  return bulletList(
    categories
      .slice()
      .sort((a, b) => b.applied_deduction - a.applied_deduction)
      .map(categoryLine),
  );
}

function processSignals(signals: FileHealthPromptSignals | null | undefined): string | null {
  if (!signals) return null;
  const lastFix = signals.last_fix_at ? `, most recently ${signals.last_fix_at.slice(0, 10)}` : "";
  return bulletList([
    signals.commit_count_90d != null
      ? `${signals.commit_count_90d} commits in the last 90 days`
      : null,
    signals.change_entropy_pct != null
      ? `Change entropy ${Math.round(signals.change_entropy_pct)}%, which is how scattered those edits were across the file`
      : null,
    signals.prior_defect_count != null && signals.prior_defect_count > 0
      ? `${signals.prior_defect_count} prior bug fixes have landed here${lastFix}`
      : null,
    signals.bug_magnet
      ? "Flagged a **bug magnet**: fixes keep landing in this file, so treat a regression here as likely rather than unlucky."
      : null,
    signals.in_degree != null
      ? `${signals.in_degree} file${pluralS(signals.in_degree)} import this one, so changing its public surface reaches all of them`
      : null,
    signals.out_degree != null ? `It imports ${signals.out_degree} others` : null,
    signals.age_days != null ? `First seen ${signals.age_days} days ago` : null,
  ]);
}

function causeLine(c: PerformanceOpportunity): string {
  // Titled the way every other surface titles a cause: the marker plus where
  // it fires. The fix's strategy name is not the cause's name, and using it
  // made distinct causes read as several copies of one.
  const first = c.evidence[0];
  const symbol = c.intervention_symbol ?? first?.function_name ?? null;
  const at = symbol ? ` in \`${symbol}\`` : "";
  const line = first?.line_start != null ? ` (line ${first.line_start})` : "";
  const reach =
    c.affected_call_sites_total > 1
      ? ` Reaches ${c.affected_call_sites_total} call sites across ${c.affected_files_total} file${pluralS(
          c.affected_files_total,
        )}.`
      : "";
  const fix = c.fix ? ` Recorded fix: ${c.fix.strategy} (${c.fix.safety}). ${c.fix.rationale}` : "";
  return `**${biomarkerInfo(c.biomarker_type).label}**${at}${line} — ${c.actionability_reason}${reach}${fix}`;
}

function performanceCauses(causes: PerformanceOpportunity[]): string | null {
  if (causes.length === 0) return null;
  return [
    bulletList(causes.slice(0, MAX_FILE_HEALTH_CAUSES).map(causeLine)),
    causes.length > MAX_FILE_HEALTH_CAUSES
      ? `…and ${causes.length - MAX_FILE_HEALTH_CAUSES} more open on this file.`
      : null,
    "These are static reads of the code, not measurements of a running system. Confirm a path is actually hot before optimising it.",
  ]
    .filter(Boolean)
    .join("\n\n");
}

/**
 * One file, everything code health has on it, as a prompt.
 *
 * Deliberately not `buildAiPrompt`. That one takes a refactoring-queue item
 * and asks for a refactor. This takes the drawer's own view of a file, which
 * spans three scored dimensions, the category ceilings, the process and
 * topology signals, and any open performance causes. Those signals disagree
 * often enough that the prompt's job is to hand over all of them and ask which
 * ones share a root cause, rather than to order a fix.
 */
export function buildFileHealthAiPrompt({
  file,
  findings = [],
  categories = [],
  signals,
  performance,
  trendDelta,
  suggestions = {},
  flavor = "generic",
  repoName,
}: BuildFileHealthPromptOptions): string {
  const { detailed, remainder, history } = openFindings(findings);
  const behaviour = [processSignals(signals), historyContextBlock(history)]
    .filter(Boolean)
    .join("\n\n");

  return joinSections([
    FLAVOR_PREAMBLE[flavor],
    `\n## Target file${repoSuffix(repoName)}\n`,
    `\`${file.file_path}\``,
    `\n## Current health snapshot\n`,
    healthSnapshot(file, categories.length, trendDelta),
    section("### Scored dimensions", scoredDimensions(file)),
    section("### Leading cause", leadingCause(file)),
    section("## Where the deduction comes from", categoryBreakdown(categories)),
    section("## Open findings (ranked by impact)", findingEntries(detailed, suggestions)),
    remainder ? `\n${remainder}` : "",
    section("## Open performance causes", performanceCauses(performance?.items ?? [])),
    section("## How this file behaves over time", behaviour),
    `\n## Hard constraints\n`,
    bulletList(CONSTRAINTS),
    `\n## What I expect back\n`,
    EXPECTED.join("\n"),
    `\n${explorationCloser(flavor, file.file_path, "file-health")}`,
  ]);
}
