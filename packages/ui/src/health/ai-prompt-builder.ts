/**
 * Build high-quality AI-agent prompts for the code-health surface.
 *
 * Two prompt kinds today:
 *   - `buildAiPrompt(target)` — refactor one file: every biomarker hit,
 *     line range, score deduction, and constraint needed to act.
 *   - `buildCoverageAiPrompt(row)` — add tests for one uncovered or
 *     under-covered file.
 *
 * Both are deliberately structured (role → file → state → tasks →
 * constraints → completion contract) so the agent doesn't have to ask
 * follow-up questions before making its first move.
 */

import { biomarkerInfo, CATEGORY_LABEL } from "./biomarker-glossary";
import type { RefactoringTarget } from "./refactoring-card";

export type AiPromptFlavor = "generic" | "claude-code" | "cursor";

const FLAVOR_PREAMBLE: Record<AiPromptFlavor, string> = {
  generic:
    "You are a senior engineer working on one file in this repository. The findings below were detected by a static analyzer — treat them as **leads, not ground truth**. Open the file, read its callers, tests, and neighbors, and verify each finding against the actual code before you act. If a finding is a false positive given the broader context, say so and skip it.",
  "claude-code":
    "You are Claude Code working in this repository. The findings below were detected by a static analyzer — treat them as leads to investigate, not commands to execute. Use Read, Grep, and Glob to explore the file, its callers, its tests, and any related modules before planning edits. Verify each finding against the actual code; flag any that turn out to be false positives. Use TodoWrite for non-trivial steps.",
  cursor:
    "Work on the file referenced below. The findings below were detected by a static analyzer — treat them as leads, not ground truth. Use @file and @codebase to read the file, its callers, its tests, and neighboring modules before editing. Verify each finding against the real code; skip and call out any false positives.",
};

function bulletList(items: (string | null | undefined | false)[]): string {
  return items.filter(Boolean).map((s) => `- ${s}`).join("\n");
}

function biomarkerExtraContext(
  biomarkerType: string,
  details: Record<string, unknown> | null | undefined,
): string | null {
  if (!details) return null;
  const numField = (k: string): number | null => {
    const v = details[k];
    if (typeof v === "number" && Number.isFinite(v)) return v;
    if (typeof v === "string" && v !== "" && Number.isFinite(Number(v))) {
      return Number(v);
    }
    return null;
  };
  const strField = (k: string): string | null => {
    const v = details[k];
    return typeof v === "string" && v.length > 0 ? v : null;
  };

  if (biomarkerType === "hidden_coupling") {
    const partner = strField("partner");
    if (!partner) return null;
    const co = numField("co_change_count");
    const corr = numField("correlation");
    const pct = corr != null ? `${Math.round(corr * 100)}%` : null;
    const tail = [
      co != null ? `${co} co-changes` : null,
      pct ? `${pct} of shared commits` : null,
    ]
      .filter(Boolean)
      .join(" — ");
    return `Partner file: \`${partner}\`${tail ? ` — ${tail}` : ""}`;
  }
  if (biomarkerType === "complex_conditional") {
    const ops = numField("operator_count");
    if (ops == null) return null;
    return `Boolean operators in this condition: ${ops}`;
  }
  if (biomarkerType === "function_hotspot") {
    const mod = numField("modification_count") ?? numField("mod_count");
    const p80 = numField("repo_p80") ?? numField("p80");
    if (mod == null) return null;
    return `Function modified across ${mod} distinct commits${p80 != null ? ` (repo p80 = ${p80})` : ""}`;
  }
  if (biomarkerType === "code_age_volatility") {
    const age = numField("median_age_days");
    const recent = numField("recent_mod_count");
    if (age == null && recent == null) return null;
    const parts: string[] = [];
    if (age != null) parts.push(`median line age ~${age} days`);
    if (recent != null) parts.push(`${recent} distinct commits in last 30 days`);
    return parts.join(", ");
  }
  return null;
}

function effortHint(effort: RefactoringTarget["effort_bucket"]): string {
  switch (effort) {
    case "S":
      return "Small (≤40 NLOC) — should be doable in one focused pass.";
    case "M":
      return "Medium (≤150 NLOC) — plan 2–3 sub-steps before editing.";
    case "L":
      return "Large (≤400 NLOC) — break into a TODO list of sub-refactors first.";
    case "XL":
      return "Extra large (>400 NLOC) — propose a staged plan and confirm scope before editing.";
  }
}

// ─────────────────────────────────────────────────────────────────────
// Refactor prompt
// ─────────────────────────────────────────────────────────────────────

export interface BuildPromptOptions {
  target: RefactoringTarget;
  flavor?: AiPromptFlavor;
  repoName?: string;
}

export function buildAiPrompt({
  target,
  flavor = "generic",
  repoName,
}: BuildPromptOptions): string {
  const t = target;
  const repoLine = repoName ? ` (\`${repoName}\`)` : "";

  const findings = (
    t.all_findings && t.all_findings.length > 0
      ? t.all_findings
      : [
          {
            id: t.primary_finding_id ?? "primary",
            biomarker_type: t.primary_biomarker,
            severity: t.primary_severity,
            function_name: t.primary_function,
            health_impact:
              t.total_impact / Math.max(t.finding_count || 1, 1),
            reason: t.primary_reason,
          },
        ]
  )
    .slice()
    .sort((a, b) => b.health_impact - a.health_impact);

  const findingsBlock = findings
    .map((f, i) => {
      const info = biomarkerInfo(f.biomarker_type);
      const loc = f.function_name
        ? `function \`${f.function_name}\`${
            "line_start" in f && (f as any).line_start
              ? ` (line ${(f as any).line_start}${(f as any).line_end ? `–${(f as any).line_end}` : ""})`
              : ""
          }`
        : "file-level";
      const extra = biomarkerExtraContext(
        f.biomarker_type,
        (f as { details?: Record<string, unknown> | null }).details,
      );
      return [
        `${i + 1}. **${info.label}** · ${CATEGORY_LABEL[info.category]} · ${f.severity.toUpperCase()} · health impact −${f.health_impact.toFixed(2)}`,
        `   - Where: ${loc}`,
        `   - Why it's a problem: ${info.description}`,
        `   - Observed: ${f.reason}`,
        extra ? `   - Extra context: ${extra}` : null,
      ]
        .filter(Boolean)
        .join("\n");
    })
    .join("\n\n");

  const constraintList = [
    "**Read first, edit second.** Read the file, its callers, its tests, and any obvious helpers before proposing a change.",
    "Do **not** change public function signatures or exported names unless absolutely required to fix a verified finding — flag it explicitly if you must.",
    "Preserve runtime behavior. Refactors only — no new features, no opportunistic rewrites in unrelated regions.",
    "Keep test coverage at least as high as before. If you change logic, add or update tests.",
    "Match the existing code style of the file and its neighbors (formatter, naming, comment density). When in doubt, check what the rest of the codebase does.",
    "Make a single coherent commit-sized change centered on this file. Touching adjacent files (tests, a tightly-coupled helper) is fine; sprawling cross-cutting edits are not — stop and propose a phased plan first.",
    "If a finding turns out to be a false positive once you've read the code, skip it and explain why in your summary.",
  ];

  const completionContract = [
    "1. A short plan (3–6 bullets) describing the structural change before any edits.",
    "2. The edits themselves, scoped to the file above (plus tests / direct helpers if needed).",
    "3. A diff-style summary of what changed and why each change reduces a specific biomarker.",
    "4. An estimate of the new biomarker state for that file: which findings should disappear, which remain.",
  ];

  return [
    FLAVOR_PREAMBLE[flavor],
    "",
    `## Target file${repoLine}`,
    "",
    `\`${t.file_path}\``,
    "",
    "## Current health snapshot",
    "",
    bulletList([
      `Health score: **${t.score.toFixed(1)}/10** (lower is worse; 10.0 is clean)`,
      `Total impact across this file: **−${t.total_impact.toFixed(2)} points** from ${t.finding_count} finding${t.finding_count === 1 ? "" : "s"}`,
      `File size: ${t.nloc} NLOC — ${effortHint(t.effort_bucket)}`,
      t.module ? `Module: \`${t.module}\`` : null,
    ]),
    "",
    "## Issues to fix (ranked by impact)",
    "",
    findingsBlock,
    "",
    t.primary_suggestion
      ? ["## Suggested direction", "", t.primary_suggestion, ""].join("\n")
      : "",
    "## Hard constraints",
    "",
    bulletList(constraintList),
    "",
    "## What I expect back",
    "",
    completionContract.join("\n"),
    "",
    "Start by reading the file end-to-end, then explore its callers, tests, and any related helpers. The findings below describe symptoms — the actual root cause may live elsewhere. Don't propose a fix until you've grounded each one in the real code.",
  ]
    .filter((s) => s !== "")
    .join("\n");
}

// ─────────────────────────────────────────────────────────────────────
// Coverage prompt
// ─────────────────────────────────────────────────────────────────────

export interface CoverageFilePromptInput {
  file_path: string;
  line_coverage_pct: number | null;
  branch_coverage_pct?: number | null;
  total_coverable_lines?: number;
  covered_lines?: number[];
  source_format?: string;
  health_score?: number | null;
  nloc?: number | null;
  module?: string | null;
}

export interface BuildCoveragePromptOptions {
  row: CoverageFilePromptInput;
  flavor?: AiPromptFlavor;
  repoName?: string;
}

function uncoveredRanges(
  covered: number[] | undefined,
  total: number | undefined,
): string {
  if (!covered || !total || covered.length === 0) return "";
  const set = new Set(covered);
  const ranges: [number, number][] = [];
  let start: number | null = null;
  for (let i = 1; i <= total; i++) {
    if (!set.has(i)) {
      if (start === null) start = i;
    } else if (start !== null) {
      ranges.push([start, i - 1]);
      start = null;
    }
  }
  if (start !== null) ranges.push([start, total]);
  if (ranges.length === 0) return "";
  // Cap to ~20 ranges so the prompt stays readable.
  const shown = ranges.slice(0, 20);
  const more = ranges.length - shown.length;
  return (
    shown.map(([a, b]) => (a === b ? `${a}` : `${a}–${b}`)).join(", ") +
    (more > 0 ? `, … (+${more} more ranges)` : "")
  );
}

export function buildCoverageAiPrompt({
  row,
  flavor = "generic",
  repoName,
}: BuildCoveragePromptOptions): string {
  const repoLine = repoName ? ` (\`${repoName}\`)` : "";
  const linePct = row.line_coverage_pct;
  const branchPct = row.branch_coverage_pct;
  const ranges = uncoveredRanges(row.covered_lines, row.total_coverable_lines);

  const constraintList = [
    "**Read first, write second.** Read the source file, the existing tests directory, and at least one nearby test file so you adopt the project's conventions instead of inventing your own.",
    "Use the project's existing test framework, fixtures, and naming conventions — don't introduce a new framework.",
    "Cover the listed uncovered branches/lines explicitly; do not just pad coverage with trivial cases.",
    "Each new test must have a clear behavior name (`should …` / `test_*_when_*`), one logical assertion focus, and no shared mutable state with other tests.",
    "Mock external IO (network, filesystem outside fixtures, time, env) — but do not mock the file under test.",
    "If you discover a real bug while writing the tests, add a failing test that documents it and call it out; do not silently fix.",
    "Trust the real source code over the coverage numbers in this prompt. If a line marked uncovered turns out to be unreachable or dead, say so and move on.",
  ];

  const completionContract = [
    "1. A short plan: which functions / branches you'll cover and in what order (3–6 bullets).",
    "2. The new tests, in the same test file location convention the project already uses.",
    "3. A coverage estimate: which uncovered ranges your new tests now hit, and which remain.",
    "4. A list of any bugs or surprising behavior you found while writing the tests.",
  ];

  return [
    FLAVOR_PREAMBLE[flavor],
    "",
    `## Target file${repoLine}`,
    "",
    `\`${row.file_path}\``,
    "",
    "## Current coverage state",
    "",
    bulletList([
      linePct == null
        ? "Line coverage: **no data** — file is not covered by any test run."
        : `Line coverage: **${linePct.toFixed(1)}%** (lower is worse)`,
      branchPct == null
        ? null
        : `Branch coverage: ${branchPct.toFixed(1)}%`,
      row.total_coverable_lines
        ? `Coverable lines: ${row.total_coverable_lines}`
        : null,
      row.nloc ? `File size: ${row.nloc} NLOC` : null,
      row.health_score != null
        ? `Current health score: ${row.health_score.toFixed(1)}/10 — risky changes here are likely to break things, so tests pay off.`
        : null,
      row.module ? `Module: \`${row.module}\`` : null,
      row.source_format ? `Coverage source: ${row.source_format.toUpperCase()}` : null,
    ]),
    "",
    ranges
      ? ["## Uncovered line ranges", "", "```", ranges, "```", ""].join("\n")
      : "",
    "## Your task",
    "",
    bulletList([
      "Add tests that cover the uncovered lines/branches listed above, prioritizing the riskiest code paths.",
      "If the file has no tests at all yet, create the test file in the project's standard location and seed it with the most important happy-path + edge cases first.",
      "Aim for a meaningful coverage jump (≥ 70% line coverage as a target), but quality of assertions matters more than the number.",
    ]),
    "",
    "## Hard constraints",
    "",
    bulletList(constraintList),
    "",
    "## What I expect back",
    "",
    completionContract.join("\n"),
    "",
    "Start by reading the file end-to-end, then explore its callers, the existing tests directory, and any sibling files that test similar code. The coverage numbers below come from a static report — verify them by looking at the real test files and the real source. Don't write a test before you've seen the code it's exercising and the project's existing test conventions.",
  ]
    .filter((s) => s !== "")
    .join("\n");
}
