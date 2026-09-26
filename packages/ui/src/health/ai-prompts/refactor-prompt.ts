import { splitByOrigin } from "../biomarker-glossary";
import type { HealthWorkItem } from "../refactoring-card";
import {
  findingEntries,
  historyContextBlock,
  rankByImpact,
  remainderRollup,
  type PromptFinding,
} from "./findings";
import {
  bulletList,
  closingSections,
  explorationCloser,
  FLAVOR_PREAMBLE,
  joinSections,
  pluralS,
  repoSuffix,
  type AiPromptFlavor,
} from "./shared";

// ─────────────────────────────────────────────────────────────────────
// Refactor prompt
// ─────────────────────────────────────────────────────────────────────

const EFFORT_HINT: Record<HealthWorkItem["effort_bucket"], string> = {
  S: "Small (≤40 NLOC) — should be doable in one focused pass.",
  M: "Medium (≤150 NLOC) — plan 2–3 sub-steps before editing.",
  L: "Large (≤400 NLOC) — break into a TODO list of sub-refactors first.",
  XL: "Extra large (>400 NLOC) — propose a staged plan and confirm scope before editing.",
};

// Findings spelled out in full; the rest roll up so the prompt stays affordable.
const MAX_DETAILED_FINDINGS = 8;

const CONSTRAINTS = [
  "**Read first, edit second.** Read the file, its callers, its tests, and any obvious helpers before proposing a change.",
  "Do **not** change public function signatures or exported names unless absolutely required to fix a verified finding — flag it explicitly if you must.",
  "Preserve runtime behavior. Refactors only — no new features, no opportunistic rewrites in unrelated regions.",
  "Keep test coverage at least as high as before. If you change logic, add or update tests.",
  "Match the existing code style of the file and its neighbors (formatter, naming, comment density). When in doubt, check what the rest of the codebase does.",
  "Make a single coherent commit-sized change centered on this file. Touching adjacent files (tests, a tightly-coupled helper) is fine; sprawling cross-cutting edits are not — stop and propose a phased plan first.",
  "If a finding turns out to be a false positive once you've read the code, skip it and explain why in your summary.",
];

const EXPECTED = [
  "1. A short plan (3–6 bullets) describing the structural change before any edits.",
  "2. The edits themselves, scoped to the file above (plus tests / direct helpers if needed).",
  "3. A diff-style summary of what changed and why each change reduces a specific marker.",
  "4. An estimate of the new marker state for that file: which findings should disappear, which remain.",
];

export interface BuildPromptOptions {
  target: HealthWorkItem;
  flavor?: AiPromptFlavor;
  repoName?: string;
}

/** The item's findings, or its primary finding when the payload omits the list. */
function targetFindings(t: HealthWorkItem): PromptFinding[] {
  if (t.all_findings && t.all_findings.length > 0) return t.all_findings;
  return [
    {
      biomarker_type: t.primary_biomarker,
      severity: t.primary_severity,
      function_name: t.primary_function,
      health_impact: t.total_impact / Math.max(t.finding_count || 1, 1),
      reason: t.primary_reason,
    },
  ];
}

function healthSnapshot(t: HealthWorkItem): string {
  return bulletList([
    `Health score: **${t.score.toFixed(1)}/10** (lower is worse; 10.0 is clean)`,
    `Total impact across this file: **−${t.total_impact.toFixed(2)} points** from ${t.finding_count} finding${pluralS(t.finding_count)}`,
    `File size: ${t.nloc} NLOC — ${EFFORT_HINT[t.effort_bucket]}`,
    t.module ? `Module: \`${t.module}\`` : null,
  ]);
}

function issuesSection(fixable: PromptFinding[]): string {
  if (fixable.length === 0) {
    return "## Issues to fix\n\nNothing in this file's own code is currently scored. Its deduction is entirely history, listed below; there is no structural work to do here.";
  }
  const detailed = fixable.slice(0, MAX_DETAILED_FINDINGS);
  const remainderLine = remainderRollup(
    fixable.slice(MAX_DETAILED_FINDINGS),
    "Clean these up after the ranked items above; open the file's full health report in repowise for the per-finding detail.",
  );
  return ["## Issues to fix (ranked by impact)", "", findingEntries(detailed), remainderLine ?? ""].join(
    "\n",
  );
}

export function buildAiPrompt({
  target,
  flavor = "generic",
  repoName,
}: BuildPromptOptions): string {
  const t = target;

  // History markers go to context, not "issues to fix"; the split keeps the ranking.
  const { codeShape: fixable, history } = splitByOrigin(rankByImpact(targetFindings(t)));
  const historyBlock = historyContextBlock(history);

  return joinSections([
    FLAVOR_PREAMBLE[flavor],
    "",
    `## Target file${repoSuffix(repoName)}`,
    "",
    `\`${t.file_path}\``,
    "",
    "## Current health snapshot",
    "",
    healthSnapshot(t),
    "",
    issuesSection(fixable),
    "",
    historyBlock ? ["## How this file behaves over time", "", historyBlock, ""].join("\n") : "",
    t.primary_suggestion
      ? ["## Suggested direction", "", t.primary_suggestion, ""].join("\n")
      : "",
    ...closingSections(CONSTRAINTS, EXPECTED),
    explorationCloser(flavor, t.file_path, "refactor"),
  ]);
}
