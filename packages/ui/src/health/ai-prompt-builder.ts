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

import { deadCodeRiskFactorLabel } from "@repowise-dev/types/dead-code";
import type { PerformanceOpportunity } from "@repowise-dev/types/health";
import type {
  OpportunityStep,
  RefactoringOpportunityDetailResolved,
  RefactoringPlan,
} from "@repowise-dev/types/refactoring";

import { biomarkerInfo, CATEGORY_LABEL } from "./biomarker-glossary";
import type { HealthWorkItem } from "./refactoring-card";
import {
  blastFiles,
  cutEdges,
  cycleMembers,
  extractClassGroups,
  extractHelperOccurrences,
  extractMethodPlan,
  helperSite,
  moveTarget,
  splitGroups,
  splitResidual,
  splitShimRequired,
} from "../refactoring/types";
import { typeMeta } from "../refactoring/meta";

export type AiPromptFlavor =
  | "generic"
  | "claude-code"
  | "claude-code-mcp"
  | "cursor";

const FLAVOR_PREAMBLE: Record<AiPromptFlavor, string> = {
  generic:
    "You are a senior engineer working on one file in this repository. The findings below were detected by a static analyzer — treat them as **leads, not ground truth**. Open the file, read its callers, tests, and neighbors, and verify each finding against the actual code before you act. If a finding is a false positive given the broader context, say so and skip it.",
  "claude-code":
    "You are Claude Code working in this repository. The findings below were detected by a static analyzer — treat them as leads to investigate, not commands to execute. Use Read, Grep, and Glob to explore the file, its callers, its tests, and any related modules before planning edits. Verify each finding against the actual code; flag any that turn out to be false positives. Use TodoWrite for non-trivial steps.",
  "claude-code-mcp":
    "You are Claude Code working in this repository, which is indexed by repowise and exposes its MCP tools. The findings below were detected by repowise's static analyzer — treat them as leads to investigate, not commands to execute. Before re-reading files by hand, pull the context repowise already computed: call `get_context([...])` for the file skeleton (every signature + the bodies of the most central symbols, ~37% of a full Read), `get_symbol(\"file::Name\")` for the exact bytes of one function, `get_risk([...])` before editing to see blast radius, co-change partners, and test gaps, and `get_why(...)` for the decision behind the current shape. Fall back to Read / Grep / Glob only for what the index can't serve. Verify each finding against the real code; flag false positives. Use TodoWrite for non-trivial steps.",
  cursor:
    "Work on the file referenced below. The findings below were detected by a static analyzer — treat them as leads, not ground truth. Use @file and @codebase to read the file, its callers, its tests, and neighboring modules before editing. Verify each finding against the real code; skip and call out any false positives.",
};

/**
 * Closing instruction, tailored per flavor. The MCP flavor steers the agent
 * to the repowise tools it already has instead of repeating the exploration
 * repowise did at index time; every other flavor keeps the read-first wording.
 */
type CloserKind = "refactor" | "coverage" | "security" | "hotspot" | "file-health";

const CLOSER_CONFIG: Record<
  CloserKind,
  { mcpSecond: (f: string) => string; mcpInto: string; verb: string; readFirst: string }
> = {
  refactor: {
    mcpSecond: (f) =>
      `\`get_risk(['${f}'])\` for the blast radius, co-change partners, and test gaps`,
    mcpInto: "functions below",
    verb: "propose a fix",
    readFirst:
      "Start by reading the file end-to-end, then explore its callers, tests, and any related helpers. The findings below describe symptoms — the actual root cause may live elsewhere. Don't propose a fix until you've grounded each one in the real code.",
  },
  coverage: {
    mcpSecond: (f) =>
      `\`get_context(['${f}'], include=['callers'])\` to see who exercises it`,
    mcpInto: "functions you'll test",
    verb: "write a test",
    readFirst:
      "Start by reading the file end-to-end, then explore its callers, the existing tests directory, and any sibling files that test similar code. The coverage numbers below come from a static report — verify them by looking at the real test files and the real source. Don't write a test before you've seen the code it's exercising and the project's existing test conventions.",
  },
  security: {
    mcpSecond: (f) =>
      `\`get_risk(['${f}'])\` to see who depends on this code before you touch it`,
    mcpInto: "flagged lines",
    verb: "change anything",
    readFirst:
      "Start by reading the file and the exact lines flagged, then trace how the value flows in and out. The scanner matches patterns — confirm this is actually exploitable in context before you change anything. If it's a false positive (test fixture, sample data, already-sanitized), say so and stop.",
  },
  hotspot: {
    mcpSecond: (f) =>
      `\`get_risk(['${f}'])\` for the co-change partners and test gaps that make this file risky to touch`,
    mcpInto: "most-churned functions",
    verb: "propose changes",
    readFirst:
      "Start by reading the file end-to-end, then look at what it co-changes with and how well it's tested. High churn is a symptom — the goal is to make this file safer and cheaper to change, not to rewrite it. Don't propose changes until you understand why it churns.",
  },
  "file-health": {
    mcpSecond: (f) =>
      `\`get_health(['${f}'])\` for the scored findings behind the numbers below, then \`get_risk(['${f}'])\` for blast radius, co-change partners and test gaps`,
    mcpInto: "functions the findings name",
    verb: "change anything",
    readFirst:
      "Start by reading the file end-to-end, then its callers, its tests, and whatever it changes alongside. The report above spans several independent signals, and they do not all point at the same fix. Work out which ones share a root cause before you touch anything.",
  },
};

/**
 * Closing instruction, tailored per surface. The MCP flavor steers the agent to
 * the repowise tools it already has instead of repeating the exploration
 * repowise did at index time; every other flavor keeps the read-first wording.
 */
function explorationCloser(
  flavor: AiPromptFlavor,
  filePath: string,
  kind: CloserKind,
): string {
  const cfg = CLOSER_CONFIG[kind];
  if (flavor === "claude-code-mcp") {
    return `Start with \`get_context(['${filePath}'])\` for the skeleton and ${cfg.mcpSecond(
      filePath,
    )}, then \`get_symbol\` into the specific ${cfg.mcpInto}. repowise already indexed this repo — lean on it before falling back to Read/Grep. Don't ${cfg.verb} until you've grounded each finding in the actual code.`;
  }
  return cfg.readFirst;
}

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

function effortHint(effort: HealthWorkItem["effort_bucket"]): string {
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
  target: HealthWorkItem;
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

  // Cap the detailed findings so a file with dozens of hits doesn't produce a
  // multi-thousand-token prompt. The top findings (by impact) are spelled out
  // in full; the long tail is rolled up into a single grouped line so the agent
  // still knows what's left without paying for every description.
  const MAX_DETAILED_FINDINGS = 8;
  const detailed = findings.slice(0, MAX_DETAILED_FINDINGS);
  const remainder = findings.slice(MAX_DETAILED_FINDINGS);

  const findingsBlock = detailed
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

  const remainderLine = (() => {
    if (remainder.length === 0) return null;
    const counts = new Map<string, number>();
    for (const f of remainder) {
      counts.set(f.biomarker_type, (counts.get(f.biomarker_type) ?? 0) + 1);
    }
    const grouped = Array.from(counts.entries())
      .sort((a, b) => b[1] - a[1])
      .map(([type, n]) => `${n}× ${biomarkerInfo(type).label}`)
      .join(", ");
    const tailImpact = remainder.reduce((s, f) => s + f.health_impact, 0);
    return `…and ${remainder.length} more lower-impact finding${
      remainder.length === 1 ? "" : "s"
    } (${grouped}; −${tailImpact.toFixed(2)} total). Clean these up after the ranked items above; open the file's full health report in repowise for the per-finding detail.`;
  })();

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
    "3. A diff-style summary of what changed and why each change reduces a specific marker.",
    "4. An estimate of the new marker state for that file: which findings should disappear, which remain.",
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
    remainderLine ?? "",
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
    explorationCloser(flavor, t.file_path, "refactor"),
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
        ? `Current health score: ${row.health_score.toFixed(1)}/10 — stronger defect indicators make focused tests especially valuable.`
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
    explorationCloser(flavor, row.file_path, "coverage"),
  ]
    .filter((s) => s !== "")
    .join("\n");
}

// ─────────────────────────────────────────────────────────────────────
// Work-queue prompt (repo-level — the Attention Needed backlog)
// ─────────────────────────────────────────────────────────────────────

const WORK_QUEUE_PREAMBLE: Record<AiPromptFlavor, string> = {
  generic:
    "You are a senior engineer triaging a backlog of issues repowise flagged across this repository. Work through them in priority order, one focused change at a time. Each item is a lead from static + git analysis — verify it against the real code before acting, and skip anything that turns out to be a false positive (say why).",
  "claude-code":
    "You are Claude Code clearing a backlog of issues repowise flagged across this repository. Use TodoWrite to track the queue, and Read / Grep / Glob to investigate each item before editing. Work in priority order, one focused, independently-revertible change at a time. Verify each item against the real code; flag false positives instead of forcing a change.",
  "claude-code-mcp":
    "You are Claude Code clearing a backlog of issues repowise flagged across this repository, which is indexed by repowise and exposes its MCP tools. Use TodoWrite to track the queue. For each item, pull the context repowise already computed — `get_context([target])` for the skeleton, `get_risk([target])` before editing, `get_why(...)` for decision items, `get_health([target])` for code-health items — instead of re-exploring by hand. Work in priority order, one focused, independently-revertible change at a time. Verify each item; flag false positives.",
  cursor:
    "You are clearing a backlog of issues repowise flagged across this repository. Work through them in priority order, one focused change at a time. Use @file and @codebase to investigate each item before editing. Verify each against the real code and skip false positives, saying why.",
};

const WORK_QUEUE_GUIDANCE: Record<string, string> = {
  stale_decision:
    "Re-check this architectural decision against the current code; update it, or supersede it if the code has moved on.",
  proposed_decision:
    "Review this auto-proposed decision: confirm it reflects reality and accept it, or reject it with a reason.",
  knowledge_silo:
    "One person holds the knowledge for this area (low bus factor). Add tests and docs that make it legible to others.",
  ungoverned_hotspot:
    "A high-churn file with no governing decision. Stabilize it (tests, clearer seams) and/or capture the decision behind it.",
  dead_code:
    "Verify with a repo-wide search (including dynamic references), then remove it in a small, revertible commit.",
};

export interface WorkQueueItem {
  type: string;
  title: string;
  description: string;
  severity: "high" | "medium" | "low";
  target_id?: string | null;
}

export interface BuildWorkQueuePromptOptions {
  items: WorkQueueItem[];
  flavor?: AiPromptFlavor;
  repoName?: string;
}

const MAX_WORK_QUEUE_ITEMS = 15;
const SEVERITY_ORDER: Record<string, number> = { high: 0, medium: 1, low: 2 };

export function buildWorkQueueAiPrompt({
  items,
  flavor = "generic",
  repoName,
}: BuildWorkQueuePromptOptions): string {
  const repoLine = repoName ? ` (\`${repoName}\`)` : "";
  const ranked = items
    .slice()
    .sort((a, b) => (SEVERITY_ORDER[a.severity] ?? 3) - (SEVERITY_ORDER[b.severity] ?? 3));
  const shown = ranked.slice(0, MAX_WORK_QUEUE_ITEMS);
  const hidden = ranked.length - shown.length;

  const itemsBlock = shown
    .map((it, i) => {
      const guidance = WORK_QUEUE_GUIDANCE[it.type];
      return [
        `${i + 1}. [${it.severity.toUpperCase()}] **${it.title}**`,
        it.description ? `   - Detail: ${it.description}` : null,
        it.target_id ? `   - Target: \`${it.target_id}\`` : null,
        guidance ? `   - How to approach: ${guidance}` : null,
      ]
        .filter(Boolean)
        .join("\n");
    })
    .join("\n\n");

  const constraintList = [
    "Work top-down by severity. Finish (or consciously defer) one item before starting the next.",
    "One focused, independently-revertible change per item — don't bundle unrelated fixes into a single commit.",
    "Verify each item against the real code first. If it's a false positive, skip it and record why instead of forcing a change.",
    "Preserve behavior. Add or update tests for anything whose logic you touch.",
    "If an item is too large for one pass, propose a phased plan for it and move on rather than half-finishing.",
  ];

  const completionContract = [
    "1. A triaged plan: the order you'll take these in and why.",
    "2. For each item you action: the change, scoped and verified, with the tests that cover it.",
    "3. For each item you skip: a one-line reason (false positive, needs product input, too large — with a proposed follow-up).",
    "4. A short summary of what's left in the queue at the end.",
  ];

  return [
    WORK_QUEUE_PREAMBLE[flavor],
    "",
    `## Repository backlog${repoLine}`,
    "",
    bulletList([
      `Items in this queue: **${ranked.length}**`,
      "Source: repowise's Attention Needed panel (decisions, hotspots, knowledge silos, dead code).",
    ]),
    "",
    "## Issues to work through (highest severity first)",
    "",
    itemsBlock,
    hidden > 0
      ? `\n…and ${hidden} more lower-priority item${hidden === 1 ? "" : "s"} in the panel — handle these after the above.`
      : "",
    "",
    "## Hard constraints",
    "",
    bulletList(constraintList),
    "",
    "## What I expect back",
    "",
    completionContract.join("\n"),
    "",
    flavor === "claude-code-mcp"
      ? "Start by calling `get_overview()` to orient, then take the queue top-down — `get_context` / `get_risk` / `get_why` per item before you touch anything. repowise already did the exploration; lean on it."
      : "Start with the highest-severity items and ground each one in the real code before acting. The list describes symptoms; confirm the root cause before you change anything.",
  ]
    .filter((s) => s !== "")
    .join("\n");
}

// ─────────────────────────────────────────────────────────────────────
// Dead-code cleanup prompt (bulk — the safe-to-delete pile)
// ─────────────────────────────────────────────────────────────────────

export interface DeadCodePromptFinding {
  file_path: string;
  symbol_name?: string | null;
  kind?: string | null;
  reason?: string | null;
  lines?: number | null;
  confidence?: number | null;
  risk_factors?: string[] | null;
}

export interface BuildDeadCodePromptOptions {
  findings: DeadCodePromptFinding[];
  flavor?: AiPromptFlavor;
  repoName?: string;
}

// Cap the file list so a big cleanup pile doesn't produce a giant prompt; the
// tail is summarized so the agent still knows the full scope.
const MAX_DEAD_CODE_FILES = 20;

export function buildDeadCodeAiPrompt({
  findings,
  flavor = "generic",
  repoName,
}: BuildDeadCodePromptOptions): string {
  const repoLine = repoName ? ` (\`${repoName}\`)` : "";

  const byFile = new Map<string, DeadCodePromptFinding[]>();
  for (const f of findings) {
    byFile.set(f.file_path, [...(byFile.get(f.file_path) ?? []), f]);
  }
  const files = Array.from(byFile.entries()).sort(
    (a, b) =>
      b[1].reduce((s, f) => s + (f.lines ?? 0), 0) -
      a[1].reduce((s, f) => s + (f.lines ?? 0), 0),
  );
  const shown = files.slice(0, MAX_DEAD_CODE_FILES);
  const hidden = files.slice(MAX_DEAD_CODE_FILES);
  const totalLines = findings.reduce((s, f) => s + (f.lines ?? 0), 0);

  const fileBlock = shown
    .map(([path, fs]) => {
      const symbols = fs.map((f) => f.symbol_name).filter(Boolean).join(", ");
      const kinds = Array.from(new Set(fs.map((f) => f.kind).filter(Boolean)));
      const reason = fs.map((f) => f.reason).filter(Boolean)[0];
      const risk = Array.from(
        new Set(fs.flatMap((f) => f.risk_factors ?? [])),
      );
      return [
        `- \`${path}\`${symbols ? ` — ${symbols}` : ""}`,
        kinds.length ? `  - Kind: ${kinds.join(", ")}` : null,
        reason ? `  - Why flagged: ${reason}` : null,
        risk.length
          ? `  - Runtime-load risk to rule out first: ${risk.map(deadCodeRiskFactorLabel).join(", ")}`
          : null,
      ]
        .filter(Boolean)
        .join("\n");
    })
    .join("\n");

  const hiddenLine =
    hidden.length > 0
      ? `…and ${hidden.length} more file${hidden.length === 1 ? "" : "s"} in the same pile (open the dead-code report in repowise for the full list).`
      : null;

  const constraintList = [
    "**Verify before deleting.** Each entry was flagged by static analysis, not proven dead. Search the whole repo (including config, DI containers, string-based imports, templates, and tests) for every symbol before removing it.",
    "Watch for dynamic access: reflection, `getattr`/`importlib`, dependency-injection registries, plugin discovery, serialization, and public-API re-exports can use code that looks unreferenced.",
    "Delete in small, reviewable commits grouped by area — not one giant sweep. Keep each commit independently revertible.",
    "Run the full test suite (and a build/type-check) after each group. If anything fails, the symbol wasn't dead — restore it and note why.",
    "Remove now-orphaned imports, fixtures, and tests that only existed for the deleted code.",
    "If a finding turns out to be reachable, mark it as a false positive in your summary instead of forcing the deletion.",
  ];

  const completionContract = [
    "1. A short plan grouping the deletions into safe, independently-revertible commits.",
    "2. The deletions themselves, with the cross-repo search you ran to confirm each one is unused.",
    "3. The test/build result after each group.",
    "4. A list of any findings you skipped as false positives, with the reference that kept them alive.",
  ];

  return [
    FLAVOR_PREAMBLE[flavor],
    "",
    `## Dead-code cleanup${repoLine}`,
    "",
    bulletList([
      `Files in this pile: **${files.length}**`,
      `Estimated reclaimable lines: **${totalLines.toLocaleString()}**`,
      "Source: repowise dead-code analysis (high-confidence, safe-to-delete tier).",
    ]),
    "",
    "## Files to clean up (largest first)",
    "",
    fileBlock,
    hiddenLine ?? "",
    "",
    "## Hard constraints",
    "",
    bulletList(constraintList),
    "",
    "## What I expect back",
    "",
    completionContract.join("\n"),
    "",
    flavor === "claude-code-mcp"
      ? "For each file, call `get_risk([...])` to see who still imports it and `get_context([...])` for its exported surface before deleting — repowise already mapped the dependency graph, so use it instead of grepping blind. A file with live dependents is not dead; surface that and skip it."
      : "Start with the largest files. For each, run a repo-wide search for its name and every exported symbol before you delete anything — the analyzer can't see dynamic or string-based references. A file with live dependents is not dead; skip it and say so.",
  ]
    .filter((s) => s !== "")
    .join("\n");
}

// ─────────────────────────────────────────────────────────────────────
// Coupling decouple prompt (per co-change pair)
// ─────────────────────────────────────────────────────────────────────

export interface CouplingPromptEdge {
  source: string;
  target: string;
  strength?: number | null;
  last_co_change?: string | null;
  /** Commits that touched both files, undecayed. */
  support?: number | null;
  /** Share of `source`'s own commits that also touched `target`. */
  confidence_ab?: number | null;
  /** The same share from `target`'s side. */
  confidence_ba?: number | null;
  /** `corroborated` / `unexplained` / `not_applicable`, or absent on an older index. */
  structural?: string | null;
}

/** What the page already knows about one end of the pair. */
export interface CouplingPromptNode {
  module?: string | null;
  score?: number | null;
  nloc?: number | null;
}

export interface BuildCouplingPromptOptions {
  edge: CouplingPromptEdge;
  flavor?: AiPromptFlavor;
  repoName?: string;
  /** Per-file facts keyed by path; either end may be missing. */
  nodes?: Record<string, CouplingPromptNode>;
}

/** The dependency-graph verdict, spelled out. `null` when the index has none. */
function structuralLine(structural: string | null | undefined): string | null {
  switch (structural) {
    case "unexplained":
      return "Dependency graph: **nothing connects them** — no import, type use, framework wiring, or read. This is the finding: they move together with no structural reason to.";
    case "corroborated":
      return "Dependency graph: a dependency already connects them, so the co-change is at least partly explained. Judge whether the dependency is the *right* one before treating this as accidental.";
    case "not_applicable":
      return "Dependency graph: at least one side was never parsed (a lockfile, changelog, config, or doc), so there was no edge to look for. The coupling is real but is probably release plumbing, not a code-structure problem.";
    default:
      return null;
  }
}

/** One end's module / health / size, as a bullet, when anything is known. */
function fileFacts(path: string, label: string, node: CouplingPromptNode | undefined): string {
  const facts: string[] = [];
  if (node?.module) facts.push(`module \`${node.module}\``);
  if (typeof node?.score === "number") facts.push(`health ${node.score.toFixed(1)}/10`);
  if (typeof node?.nloc === "number" && node.nloc > 0) facts.push(`${node.nloc} lines`);
  return facts.length
    ? `File ${label}: \`${path}\` (${facts.join(", ")})`
    : `File ${label}: \`${path}\``;
}

export function buildCouplingAiPrompt({
  edge,
  flavor = "generic",
  repoName,
  nodes,
}: BuildCouplingPromptOptions): string {
  const repoLine = repoName ? ` (\`${repoName}\`)` : "";
  const last = edge.last_co_change
    ? new Date(edge.last_co_change).toISOString().slice(0, 10)
    : null;
  const pctOf = (v: number | null | undefined) =>
    typeof v === "number" ? `${Math.round(v * 100)}%` : null;
  const abPct = pctOf(edge.confidence_ab);
  const baPct = pctOf(edge.confidence_ba);

  const constraintList = [
    "**Diagnose before decoupling.** Read both files and the commits that touched them together. The coupling may be legitimate (two halves of one feature) or accidental (a leaky abstraction, a shared constant, copy-paste). Name which it is before acting.",
    "If it's accidental, fix the cause: extract the shared concept into one owner, invert the dependency, or introduce a stable interface — don't just move code around.",
    "If it's legitimate and unavoidable, say so and stop. Forcing a split that the domain doesn't support makes things worse.",
    "Preserve behavior. This is a structural change, not a feature change.",
    "Add or update tests so the new boundary is exercised and the old hidden contract can't silently regress.",
  ];

  const completionContract = [
    "1. A verdict: is this coupling accidental or legitimate, and what's the underlying shared concern?",
    "2. If accidental — a concrete decoupling plan (extract / invert / interface), smallest-risk first.",
    "3. The first change, scoped and behavior-preserving, with its tests.",
    "4. If legitimate — the reason to leave it, and any lighter-touch improvement (docs, a shared module) worth doing instead.",
  ];

  const closer =
    flavor === "claude-code-mcp"
      ? `Call \`get_risk(['${edge.source}'])\` and \`get_context(['${edge.source}', '${edge.target}'])\` to see what else each file pulls in before you plan the split — repowise already mapped the dependency graph and the co-change history. Don't restructure until you know why they move together.`
      : "Start by reading both files and `git log` for the commits that changed them together. The co-change count is a symptom; find the shared concern driving it before you restructure anything.";

  return [
    FLAVOR_PREAMBLE[flavor],
    "",
    `## Hidden coupling to untangle${repoLine}`,
    "",
    bulletList([
      fileFacts(edge.source, "A", nodes?.[edge.source]),
      fileFacts(edge.target, "B", nodes?.[edge.target]),
      edge.support
        ? `Shared commits: **${edge.support}** (undecayed count of commits that touched both)`
        : null,
      // The asymmetry is the content: a file that never changes alone is a
      // different finding from two that both change often.
      abPct && baPct
        ? `Directional confidence: ${abPct} of A's own commits also touched B; ${baPct} of B's also touched A. The larger share is the stronger claim; the smaller one says how independent that side still is.`
        : (abPct ?? baPct)
          ? `Directional confidence: ${abPct ? `${abPct} of A's own commits also touched B` : `${baPct} of B's own commits also touched A`} (the other side's commit total is unknown).`
          : null,
      structuralLine(edge.structural),
      edge.strength != null
        ? `Coupling strength: ${edge.strength} (recency-weighted, not a percentage and not a verified dependency)`
        : null,
      last ? `Last changed together: ${last}` : null,
      "Source: repowise co-change analysis (git history — treat as a lead).",
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
    closer,
  ]
    .filter((s) => s !== "")
    .join("\n");
}

// ─────────────────────────────────────────────────────────────────────
// Conformance prompt (bulk — architecture rule violations)
// ─────────────────────────────────────────────────────────────────────

export interface ConformancePromptViolation {
  source: string;
  target: string;
  source_name?: string | null;
  target_name?: string | null;
  edge_kind?: string | null;
  rule_source?: string | null;
  rule_target?: string | null;
  rule_description?: string | null;
}

export interface BuildConformancePromptOptions {
  violations: ConformancePromptViolation[];
  flavor?: AiPromptFlavor;
  repoName?: string;
}

const MAX_CONFORMANCE_VIOLATIONS = 20;

export function buildConformanceAiPrompt({
  violations,
  flavor = "generic",
  repoName,
}: BuildConformancePromptOptions): string {
  const wsLine = repoName ? ` (\`${repoName}\`)` : "";
  const shown = violations.slice(0, MAX_CONFORMANCE_VIOLATIONS);
  const hidden = violations.length - shown.length;

  const block = shown
    .map((v, i) => {
      const src = v.source_name || v.source;
      const tgt = v.target_name || v.target;
      const rule =
        v.rule_source && v.rule_target
          ? `${v.rule_source} !-> ${v.rule_target}`
          : null;
      return [
        `${i + 1}. **${src} → ${tgt}**${v.edge_kind ? ` (${v.edge_kind})` : ""}`,
        rule ? `   - Breaks rule: \`${rule}\`` : null,
        v.rule_description ? `   - Rule intent: ${v.rule_description}` : null,
      ]
        .filter(Boolean)
        .join("\n");
    })
    .join("\n\n");

  const constraintList = [
    "**Fix the architecture, not the rule.** The goal is to remove the disallowed dependency, not to relax the declared rule (unless the rule is genuinely wrong — if so, say so and stop).",
    "For each violation, find the actual import/call/event that creates the edge, then choose the right fix: invert the dependency, introduce a shared interface/contract module, move the shared code to an allowed layer, or route through an allowed intermediary.",
    "Preserve behavior. These are structural changes across service boundaries — keep the public contract stable.",
    "Tackle one violation (or one tightly-related cluster) per change so each is reviewable and revertible.",
    "Update or add tests that would catch the boundary regressing again.",
  ];

  const completionContract = [
    "1. For each violation: the exact code that creates the disallowed edge (file + symbol).",
    "2. The chosen fix and why it's the right one (invert / interface / move / intermediary).",
    "3. The change itself, scoped per violation, with tests.",
    "4. Any violation you believe reflects a wrong rule rather than wrong code, with your reasoning.",
  ];

  return [
    FLAVOR_PREAMBLE[flavor],
    "",
    `## Architecture conformance violations${wsLine}`,
    "",
    bulletList([
      `Violations: **${violations.length}** dependencies that break a declared rule.`,
      "Source: repowise workspace conformance check (live dependency graph vs declared rules).",
    ]),
    "",
    "## Violations to resolve",
    "",
    block,
    hidden > 0
      ? `\n…and ${hidden} more violation${hidden === 1 ? "" : "s"} — resolve these after the above.`
      : "",
    "",
    "## Hard constraints",
    "",
    bulletList(constraintList),
    "",
    "## What I expect back",
    "",
    completionContract.join("\n"),
    "",
    flavor === "claude-code-mcp"
      ? "For each violation, use `get_blast_radius` / `get_context` on the two services to find the exact edge and what else rides on it before you cut it — repowise already resolved the cross-repo graph. Don't restructure blind."
      : "For each violation, locate the real import/call/event that creates the edge before proposing a fix. The rule names the boundary; the code is where you'll actually sever it.",
  ]
    .filter((s) => s !== "")
    .join("\n");
}

// ─────────────────────────────────────────────────────────────────────
// Security remediation prompt (per finding)
// ─────────────────────────────────────────────────────────────────────

export interface SecurityPromptFinding {
  file_path: string;
  kind: string;
  severity: string;
  snippet?: string | null;
}

export interface BuildSecurityPromptOptions {
  finding: SecurityPromptFinding;
  flavor?: AiPromptFlavor;
  repoName?: string;
}

export function buildSecurityAiPrompt({
  finding,
  flavor = "generic",
  repoName,
}: BuildSecurityPromptOptions): string {
  const repoLine = repoName ? ` (\`${repoName}\`)` : "";
  const isSecret = /secret|key|token|credential|password/i.test(finding.kind);

  const constraintList = [
    "**Confirm it's real first.** Reproduce the issue or trace the data flow before editing. Pattern scanners over-flag — test fixtures, sample data, and already-sanitized paths are common false positives. If this is one, say so and stop.",
    isSecret
      ? "If this is a live secret, the fix is two-part: (1) remove it from the code and load it from a secret manager / env var, and (2) call out that the secret must be **rotated** — it is compromised the moment it lands in git history."
      : "Fix the root cause, not the symptom — validate/escape/parameterize at the boundary rather than blocking one known-bad input.",
    "Preserve behavior for legitimate inputs. Don't break the feature to silence the scanner.",
    "Add or update a test that fails on the vulnerable behavior and passes after the fix, where the project's setup allows it.",
    "Don't introduce a new dependency for this unless there's no safe stdlib/first-party option; if you do, justify it.",
  ];

  const completionContract = [
    "1. A one-line verdict: is this exploitable, and how (or why it's a false positive)?",
    "2. The fix, scoped to the smallest change that closes the issue.",
    "3. The test that now covers it, if one was feasible.",
    isSecret ? "4. An explicit rotation/remediation note for the exposed secret." : "4. Any related spots in the codebase with the same pattern that should get the same fix.",
  ];

  return [
    FLAVOR_PREAMBLE[flavor],
    "",
    `## Security finding${repoLine}`,
    "",
    bulletList([
      `File: \`${finding.file_path}\``,
      `Type: **${finding.kind}**`,
      `Severity: **${finding.severity.toUpperCase()}**`,
      "Source: repowise local security scan (pattern-based — treat as a lead).",
    ]),
    "",
    finding.snippet
      ? ["## Flagged code", "", "```", finding.snippet, "```", ""].join("\n")
      : "",
    "## Your task",
    "",
    `Investigate and remediate this ${finding.kind} finding in \`${finding.file_path}\`.`,
    "",
    "## Hard constraints",
    "",
    bulletList(constraintList),
    "",
    "## What I expect back",
    "",
    completionContract.join("\n"),
    "",
    explorationCloser(flavor, finding.file_path, "security"),
  ]
    .filter((s) => s !== "")
    .join("\n");
}

// ─────────────────────────────────────────────────────────────────────
// Hotspot stabilization prompt (per file)
// ─────────────────────────────────────────────────────────────────────

export interface HotspotPromptInput {
  file_path: string;
  churn_percentile?: number | null;
  commit_count_90d?: number | null;
  commit_count_30d?: number | null;
  bus_factor?: number | null;
  contributor_count?: number | null;
  primary_owner?: string | null;
  lines_added_90d?: number | null;
  lines_deleted_90d?: number | null;
  temporal_hotspot_score?: number | null;
  change_entropy_pct?: number | null;
  prior_defect_count?: number | null;
  module?: string | null;
}

export interface BuildHotspotPromptOptions {
  hotspot: HotspotPromptInput;
  flavor?: AiPromptFlavor;
  repoName?: string;
}

export function buildHotspotAiPrompt({
  hotspot: h,
  flavor = "generic",
  repoName,
}: BuildHotspotPromptOptions): string {
  const repoLine = repoName ? ` (\`${repoName}\`)` : "";
  const soleOwner = h.bus_factor != null && h.bus_factor <= 1;

  const constraintList = [
    "**Understand the churn before touching it.** A hotspot is a file that keeps changing — find out why (a god module, mixed responsibilities, a leaky abstraction, missing tests) before proposing structure changes.",
    "Make it safer to change, don't just rewrite it. Behavior-preserving refactors, better seams, and tests beat a from-scratch rewrite.",
    soleOwner
      ? "This file has a low bus factor (one person holds the knowledge). Favor changes that make it more legible to others — clear names, docs on the non-obvious parts, tests that document intent."
      : "Keep the change reviewable — a single coherent improvement, not a sprawling rewrite.",
    "Because this file changes often, raise its test coverage as part of the work — that's what makes future changes cheap.",
    "Check its co-change partners: if it always changes alongside another file, the coupling itself may be the thing to fix.",
  ];

  const completionContract = [
    "1. A diagnosis: why does this file churn so much? (2–4 bullets, grounded in the actual code and its history.)",
    "2. A prioritized plan to reduce its change-cost — structural seams, extractions, or decoupling, smallest-risk first.",
    "3. The change you'd make first, scoped and behavior-preserving, with the tests that protect it.",
    "4. What you'd leave for later and why.",
  ];

  return [
    FLAVOR_PREAMBLE[flavor],
    "",
    `## Hotspot to stabilize${repoLine}`,
    "",
    `\`${h.file_path}\``,
    "",
    "## Why it's flagged",
    "",
    bulletList([
      h.churn_percentile != null
        ? `Churn: **${Math.round(h.churn_percentile)}th percentile** in this repo (it changes more than most files)`
        : null,
      h.commit_count_90d != null
        ? `Commits: **${h.commit_count_90d} in 90 days**${h.commit_count_30d != null ? ` (${h.commit_count_30d} in the last 30)` : ""}`
        : null,
      h.bus_factor != null
        ? `Bus factor: **${h.bus_factor}**${soleOwner ? " — knowledge concentrated in one person" : ""}`
        : null,
      h.contributor_count != null ? `Contributors: ${h.contributor_count}` : null,
      h.primary_owner ? `Primary owner: ${h.primary_owner}` : null,
      h.lines_added_90d != null || h.lines_deleted_90d != null
        ? `Lines churned (90d): +${h.lines_added_90d ?? 0} / −${h.lines_deleted_90d ?? 0}`
        : null,
      h.change_entropy_pct != null
        ? `Change entropy: ${Math.round(h.change_entropy_pct)}th percentile (how scattered the edits are)`
        : null,
      h.prior_defect_count != null && h.prior_defect_count > 0
        ? `Prior bug-fix commits here: ${h.prior_defect_count}`
        : null,
      h.module ? `Module: \`${h.module}\`` : null,
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
    explorationCloser(flavor, h.file_path, "hotspot"),
  ]
    .filter((s) => s !== "")
    .join("\n");
}

// ─────────────────────────────────────────────────────────────────────
// Decision verification prompt (per architectural decision)
// ─────────────────────────────────────────────────────────────────────

export interface DecisionPromptInput {
  title: string;
  status: string;
  context?: string | null;
  decision?: string | null;
  rationale?: string | null;
  alternatives?: string[];
  consequences?: string[];
  affected_modules?: string[];
  affected_files?: string[];
  staleness_score?: number | null;
  confidence?: number | null;
}

export interface BuildDecisionPromptOptions {
  decision: DecisionPromptInput;
  flavor?: AiPromptFlavor;
  repoName?: string;
}

export function buildDecisionAiPrompt({
  decision: d,
  flavor = "generic",
  repoName,
}: BuildDecisionPromptOptions): string {
  const repoLine = repoName ? ` (\`${repoName}\`)` : "";
  const isProposed = d.status === "proposed";
  const isStale = (d.staleness_score ?? 0) > 0.5;
  const scope = [...(d.affected_modules ?? []), ...(d.affected_files ?? [])];

  const task = isProposed
    ? "repowise auto-proposed this architectural decision from the code and history. Verify it: does the codebase actually reflect this decision today? Then recommend whether to **confirm** it (it's real and current) or **reject** it (it's wrong, speculative, or already superseded)."
    : isStale
      ? "This recorded decision is flagged stale — the code it governs has changed since it was written. Re-verify it against the current code and recommend whether to **keep**, **update**, or **deprecate** it."
      : "Verify this recorded decision against the current code: is it still honored in the implementation? Recommend whether to keep, update, or deprecate it.";

  const constraintList = [
    "Ground every claim in the actual code, not the decision text. The decision describes intent; the code is the truth. Where they disagree, the code wins and the decision is stale.",
    scope.length > 0
      ? "Start from the affected modules/files listed below, then follow the dependency graph to anything that should obey this decision but doesn't."
      : "Identify which parts of the codebase this decision governs, then check them for conformance.",
    "Cite specific files/symbols as evidence for your verdict — don't assert without a reference.",
    "Distinguish 'the decision is wrong' from 'the code drifted from a still-good decision' — they lead to opposite actions (reject/deprecate vs. fix the code).",
    "Do not change code as part of this task unless asked — this is a verification, not an implementation.",
  ];

  const completionContract = [
    `1. A verdict: ${isProposed ? "**confirm** or **reject**" : "**keep**, **update**, or **deprecate**"}, in one line.`,
    "2. The evidence: the files/symbols you checked and whether each conforms.",
    "3. Any conformance gaps — places that violate the decision — as a short list.",
    "4. If you'd update the decision text, the exact wording you'd change.",
  ];

  const closer =
    flavor === "claude-code-mcp"
      ? `Use \`get_why('${d.title.replace(/'/g, "")}')\` for the recorded rationale and \`get_context([${scope.slice(0, 3).map((s) => `'${s}'`).join(", ")}])\` for the governed code — repowise links decisions to graph nodes, so verify against that instead of guessing. Then check conformance file by file.`
      : "Read the decision below, then open the code it governs and check it line up. The decision is a claim about the code — your job is to confirm or refute it with evidence.";

  return [
    FLAVOR_PREAMBLE[flavor],
    "",
    `## Architectural decision to verify${repoLine}`,
    "",
    bulletList([
      `Title: **${d.title}**`,
      `Status: ${d.status}`,
      d.confidence != null ? `Recorded confidence: ${Math.round(d.confidence * 100)}%` : null,
      isStale ? `Staleness: ${(d.staleness_score ?? 0).toFixed(2)} — flagged stale` : null,
      scope.length > 0 ? `Governs: ${scope.slice(0, 8).map((s) => `\`${s}\``).join(", ")}${scope.length > 8 ? `, +${scope.length - 8} more` : ""}` : null,
    ]),
    "",
    d.context ? ["## Context", "", d.context, ""].join("\n") : "",
    d.decision ? ["## Decision", "", d.decision, ""].join("\n") : "",
    d.rationale ? ["## Rationale", "", d.rationale, ""].join("\n") : "",
    d.alternatives && d.alternatives.length > 0
      ? ["## Alternatives rejected", "", bulletList(d.alternatives), ""].join("\n")
      : "",
    d.consequences && d.consequences.length > 0
      ? ["## Consequences", "", bulletList(d.consequences), ""].join("\n")
      : "",
    "## Your task",
    "",
    task,
    "",
    "## Hard constraints",
    "",
    bulletList(constraintList),
    "",
    "## What I expect back",
    "",
    completionContract.join("\n"),
    "",
    closer,
  ]
    .filter((s) => s !== "")
    .join("\n");
}

/**
 * Enforcement sibling of {@link buildDecisionAiPrompt}: where the
 * verification prompt asks "is this decision still true?", this one asks the
 * agent to bring non-conforming code into line with the decision — the
 * follow-through once a decision is confirmed.
 */
export function buildDecisionEnforcementAiPrompt({
  decision: d,
  flavor = "generic",
  repoName,
}: BuildDecisionPromptOptions): string {
  const repoLine = repoName ? ` (\`${repoName}\`)` : "";
  const scope = [...(d.affected_modules ?? []), ...(d.affected_files ?? [])];

  const constraintList = [
    "Treat the decision text below as the standard; your job is conformance, not re-litigating whether the decision is right. If you find strong evidence the decision itself is wrong, stop and report that instead of enforcing it.",
    scope.length > 0
      ? "Start from the affected modules/files listed below, then follow the dependency graph to anything else that should obey this decision."
      : "First identify which parts of the codebase this decision governs, then audit them.",
    "Cite specific files/symbols for every violation — don't assert without a reference.",
    "Propose the minimal change that brings each violation into conformance; don't refactor beyond what the decision requires.",
    "Keep behavior identical except where the decision explicitly requires otherwise; call out any user-visible change.",
  ];

  const completionContract = [
    "1. A conformance audit: each governed file/module and whether it conforms or violates the decision, with evidence.",
    "2. For every violation, the concrete fix (file, symbol, and the change), ordered by impact.",
    "3. The fixes applied (or, if you can't safely change something, why, and what a human should do).",
    "4. Anything governed by the decision that you couldn't check, so nothing is silently skipped.",
  ];

  const closer =
    flavor === "claude-code-mcp"
      ? `Use \`get_why('${d.title.replace(/'/g, "")}')\` for the recorded rationale and \`get_context([${scope.slice(0, 3).map((s) => `'${s}'`).join(", ")}])\` for the governed code — repowise links decisions to graph nodes, so audit against that instead of guessing.`
      : "Read the decision below, open the code it governs, and make the code match the decision — with a cited audit trail for every change.";

  return [
    FLAVOR_PREAMBLE[flavor],
    "",
    `## Architectural decision to enforce${repoLine}`,
    "",
    bulletList([
      `Title: **${d.title}**`,
      `Status: ${d.status}`,
      scope.length > 0
        ? `Governs: ${scope.slice(0, 8).map((s) => `\`${s}\``).join(", ")}${scope.length > 8 ? `, +${scope.length - 8} more` : ""}`
        : null,
    ]),
    "",
    d.context ? ["## Context", "", d.context, ""].join("\n") : "",
    d.decision ? ["## Decision", "", d.decision, ""].join("\n") : "",
    d.rationale ? ["## Rationale", "", d.rationale, ""].join("\n") : "",
    d.consequences && d.consequences.length > 0
      ? ["## Consequences", "", bulletList(d.consequences), ""].join("\n")
      : "",
    "## Your task",
    "",
    "Audit the governed code for compliance with this decision, then fix every violation you find. This is an enforcement pass: the outcome should be code that conforms, not just a report.",
    "",
    "## Hard constraints",
    "",
    bulletList(constraintList),
    "",
    "## What I expect back",
    "",
    completionContract.join("\n"),
    "",
    closer,
  ]
    .filter((s) => s !== "")
    .join("\n");
}

// ─────────────────────────────────────────────────────────────────────
// Commit review prompt (per commit)
// ─────────────────────────────────────────────────────────────────────

export interface CommitPromptInput {
  sha: string;
  subject: string;
  review_priority?: string | null;
  risk_percentile?: number | null;
  change_risk_score?: number | null;
  is_fix?: boolean;
  files_changed?: number | null;
  lines_added?: number | null;
  lines_deleted?: number | null;
  entropy?: number | null;
  top_drivers?: string[];
  author_name?: string | null;
}

export interface BuildCommitPromptOptions {
  commit: CommitPromptInput;
  flavor?: AiPromptFlavor;
  repoName?: string;
}

export function buildCommitAiPrompt({
  commit: c,
  flavor = "generic",
  repoName,
}: BuildCommitPromptOptions): string {
  const repoLine = repoName ? ` (\`${repoName}\`)` : "";
  const short = c.sha.slice(0, 10);

  const constraintList = [
    "Read the diff first. The calibrated diff-size score is supporting evidence, not a verdict — a high score on a mechanical rename is fine; a low score hiding a logic change is not.",
    "Focus on what the change-risk drivers flag: scattered edits, missing tests, a hotspot touch, a new-to-the-area author. Confirm each against the actual diff.",
    "Check the blast radius: what depends on the changed files, and is anything that usually changes with them missing from this commit?",
    "Call out missing or weak test coverage for the behavior this commit changes.",
    "Be specific — reference files and lines. A review that says 'looks risky' is useless.",
  ];

  const completionContract = [
    "1. A one-paragraph risk read: is this commit actually risky, and where?",
    "2. The specific things a reviewer should scrutinize, as a checklist tied to files.",
    "3. Suggested reviewers — who owns or recently changed the affected code.",
    "4. Any missing tests or co-change partners that should have been in this commit.",
  ];

  const closer =
    flavor === "claude-code-mcp"
      ? `Call \`get_risk(changed_files=[...])\` for this commit's files to get the blast radius, co-change partners, missing-test directive, and owners in one shot — repowise computes all of that. Then read the diff and ground each flag.`
      : `Start with \`git show ${short}\` to read the diff, then check who owns and recently touched the changed files. Ground every risk call in the actual change.`;

  return [
    FLAVOR_PREAMBLE[flavor],
    "",
    `## Commit to review${repoLine}`,
    "",
    bulletList([
      `Commit: \`${short}\` — ${c.subject || "(no subject)"}`,
      c.author_name ? `Author: ${c.author_name}` : null,
      c.is_fix ? "Tagged as a bug-fix commit." : null,
      c.review_priority ? `Review priority (repo-relative): **${c.review_priority}**` : null,
      c.risk_percentile != null ? `Diff-shape percentile in this repo: ${Math.round(c.risk_percentile)}th` : null,
      c.change_risk_score != null ? `Supporting diff-size score: ${c.change_risk_score.toFixed(1)}/10 (calibrated per commit)` : null,
      c.files_changed != null ? `Files changed: ${c.files_changed}` : null,
      c.lines_added != null || c.lines_deleted != null
        ? `Lines: +${c.lines_added ?? 0} / −${c.lines_deleted ?? 0}`
        : null,
      c.entropy != null ? `Change entropy: ${c.entropy.toFixed(2)} (how scattered the edits are)` : null,
      c.top_drivers && c.top_drivers.length > 0
        ? `Top risk drivers: ${c.top_drivers.slice(0, 3).join(", ")}`
        : null,
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
    closer,
  ]
    .filter((s) => s !== "")
    .join("\n");
}

// ─────────────────────────────────────────────────────────────────────
// Refactoring plan prompt — hand a deterministic plan to a coding agent
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
    "## What to do",
    "",
    "1. Verify the repeated cost and caller-to-sink paths against the real code.",
    "2. Determine whether one behavior-preserving intervention safely addresses the shared cause.",
    "3. Identify the tests and commands that prove result equivalence and performance improvement.",
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

function planSourceLink(path: string, start: number | null, end: number | null): string {
  if (start && end) return `\`${path}:${start}-${end}\``;
  if (start) return `\`${path}:${start}\``;
  return `\`${path}\``;
}

function recommendationValidation(plan: RefactoringPlan): string {
  const validation = plan.validation;
  if (!validation) return "";
  const evidence =
    validation.basis === "unknown"
      ? "No measured or inferred guarding test was found; treat this as a validation gap."
      : `${validation.total} guarding test${validation.total === 1 ? "" : "s"} via ${
          validation.via ?? validation.basis
        }${validation.truncated ? ` (showing ${validation.tests.length})` : ""}.`;
  return [
    "## Validation plan",
    "",
    bulletList([
      evidence,
      validation.tests.length
        ? `Tests: ${validation.tests.map((test) => `\`${test}\``).join(", ")}.`
        : null,
      validation.affected_files.length
        ? `Affected files: ${validation.affected_files
            .map((file) => `\`${file}\``)
            .join(", ")}.`
        : null,
      validation.affected_symbols.length
        ? `Affected symbols: ${validation.affected_symbols
            .map((symbol) => `\`${symbol}\``)
            .join(", ")}.`
        : null,
      validation.commands.length
        ? `Run: ${validation.commands.map((command) => `\`${command}\``).join("; ")}.`
        : null,
    ]),
  ].join("\n");
}

/** Render the concrete, type-specific steps the agent should carry out. The
 *  detection is already done deterministically; this is the executable plan. */
function refactoringPlanSteps(plan: RefactoringPlan): string {
  switch (plan.refactoring_type) {
    case "extract_class": {
      const groups = extractClassGroups(plan).filter(
        (g) => g.methods.length > 0 || g.fields.length > 0,
      );
      const lines = groups.map((g, i) => {
        const name = g.name ?? `NewClass${i + 1}`;
        const methods = g.methods.length ? g.methods.join(", ") : "(none)";
        const fields = g.fields.length ? g.fields.join(", ") : "(none)";
        return `- **${name}** — methods: ${methods}; fields: ${fields}`;
      });
      return [
        `Split \`${plan.target_symbol}\` into ${groups.length} cohesive class${
          groups.length === 1 ? "" : "es"
        }, one per group below. Each group's methods and the fields they touch move together:`,
        "",
        lines.join("\n"),
        "",
        "Pick a clear name for each group (the `NewClass*` placeholders are not final), keep the original class as a thin facade or update call sites, and preserve behavior.",
      ].join("\n");
    }
    case "extract_helper": {
      const occ = extractHelperOccurrences(plan);
      const site = helperSite(plan);
      const lines = occ.map((o) => `- ${planSourceLink(o.file, o.line_start, o.line_end)}`);
      return [
        `Extract the duplicated block (${occ.length} occurrence${
          occ.length === 1 ? "" : "s"
        }) into one shared helper${site ? ` near \`${site}\`` : ""}:`,
        "",
        lines.join("\n"),
        "",
        "Define the helper once, replace every occurrence with a call to it, and confirm the behavior is identical at each site (watch for small per-site differences that need a parameter).",
      ].join("\n");
    }
    case "extract_method": {
      const em = extractMethodPlan(plan);
      if (!em.span) return "Extract the indicated slice into a helper method.";
      const params = em.params.length ? em.params.join(", ") : "(none)";
      const returns = em.returns.length ? em.returns.join(", ") : "(nothing)";
      const name = em.suggested_name ?? "a clearly named helper";
      return [
        `Extract lines ${em.span.start}–${em.span.end} of \`${plan.target_symbol}\` into ${name}:`,
        "",
        `- **Parameters (in):** ${params}`,
        `- **Returns (out):** ${returns}`,
        "",
        "Move exactly those lines into the new helper in the same scope, pass the parameters above, return the value(s) above, and replace the original lines with a single call to it. Preserve behavior exactly: change nothing outside the span and that one call site.",
      ].join("\n");
    }
    case "move_method": {
      const mv = moveTarget(plan);
      if (!mv) return "Move the method to the class it belongs to.";
      return [
        `Move \`${mv.method}\` from \`${mv.from_class}\` to \`${mv.to_class}\`${
          mv.to_file ? ` (in \`${mv.to_file}\`)` : ""
        }.`,
        "",
        "The method uses the target class's data more than its own. Move it, update both classes, and fix every call site. Only do this if the target class is legally accessible from the call sites.",
      ].join("\n");
    }
    case "break_cycle": {
      const members = cycleMembers(plan);
      const edges = cutEdges(plan);
      const edgeLines = edges.map((e) => `- \`${e.from}\` → \`${e.to}\``);
      return [
        `Break the import cycle across ${members.length} file${
          members.length === 1 ? "" : "s"
        } by cutting ${edges.length} edge${edges.length === 1 ? "" : "s"}:`,
        "",
        edgeLines.join("\n"),
        "",
        "For each edge, invert the dependency or introduce an abstraction/interface so the importer no longer needs the importee at module load time. Don't just move the import inside a function unless that genuinely breaks the cycle.",
      ].join("\n");
    }
    case "split_file": {
      const groups = splitGroups(plan).filter((g) => g.symbols.length > 0);
      const residual = splitResidual(plan);
      const shim = splitShimRequired(plan);
      const lines = groups.map(
        (g, i) => `- **${g.suggested_file ?? `part${i + 1}`}** — ${g.symbols.join(", ")}`,
      );
      return [
        `Split \`${plan.file_path}\` into ${groups.length} file${
          groups.length === 1 ? "" : "s"
        } along the seams below. Move each group's symbols together:`,
        "",
        lines.join("\n"),
        residual.length
          ? `\nLeave in the original file (shared by more than one group): ${residual
              .map((symbol) => `\`${symbol}\``)
              .join(", ")}.`
          : "",
        "",
        shim
          ? "Other files import this module, so keep the original path importable: re-export the moved symbols from it, or update every importer. Do not break the existing import surface."
          : "Update every importer of the moved symbols.",
        "The suggested filenames are a starting point, not a requirement; rename them if the repo has a clearer convention.",
      ]
        .filter((line) => line !== "")
        .join("\n");
    }
    default:
      return "Apply the refactoring described above.";
  }
}

/**
 * Build a ready-to-paste prompt that hands a coding agent ONE deterministic
 * refactoring plan: what to change, the concrete per-type steps, the blast
 * radius it must keep consistent, and a completion contract. Unlike the
 * file-level fix prompt, the plan here is already computed — the agent's job is
 * to execute it and verify behavior, not to rediscover the smell.
 */
export function buildRefactoringPlanPrompt({
  plan,
  flavor = "generic",
  repoName,
}: BuildRefactoringPlanPromptOptions): string {
  const repoLine = repoName ? ` (\`${repoName}\`)` : "";
  const meta = typeMeta(plan.refactoring_type);
  const files = blastFiles(plan).filter((f) => f !== plan.file_path);
  const validation = recommendationValidation(plan);

  const constraintList = [
    "Preserve behavior exactly — this is a refactoring, not a feature change. No public API or observable behavior should shift.",
    "Run the project's tests (and type-checker/linter) after the change; the suite must stay green.",
    "If, after reading the real code, the plan looks wrong or unsafe, stop and explain why instead of forcing it — the detection is static and can be a false positive.",
    files.length > 0
      ? `Keep these co-affected files consistent: ${files.map((f) => `\`${f}\``).join(", ")}.`
      : null,
  ];

  const completionContract = [
    "1. The refactored code, with each step above applied.",
    "2. A short note on what you renamed/introduced and why.",
    "3. Confirmation the tests pass (or the exact failures if they don't).",
    "4. Any call sites or co-changed files you had to update.",
  ];

  return [
    FLAVOR_PREAMBLE[flavor],
    "",
    `## ${meta.label}${repoLine}`,
    "",
    bulletList([
      `Target: ${planSourceLink(plan.file_path, plan.line_start, plan.line_end)}${
        plan.target_symbol ? ` — \`${plan.target_symbol}\`` : ""
      }`,
      `What: ${meta.blurb}`,
      plan.impact_delta > 0
        ? `Recovers ~${plan.impact_delta.toFixed(2)} of health score if applied.`
        : null,
      plan.effort_bucket ? `Effort: ${plan.effort_bucket} bucket.` : null,
      plan.confidence ? `Detector confidence: ${plan.confidence}.` : null,
    ]),
    "",
    "## The plan",
    "",
    refactoringPlanSteps(plan),
    "",
    validation,
    "",
    "## Hard constraints",
    "",
    bulletList(constraintList),
    "",
    "## What I expect back",
    "",
    completionContract.join("\n"),
    "",
    explorationCloser(flavor, plan.file_path, "refactor"),
  ]
    .filter((s) => s !== "")
    .join("\n");
}

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

/**
 * The ordered steps, each one saying what kind of change it is.
 *
 * `relocated_by` gets its own sentence rather than a footnote. A step it marks
 * names an earlier step that moves its symbol to another file, so its own path
 * and span describe where the symbol *was* — an agent that follows those
 * coordinates after applying step one lands in the wrong file, and nothing else
 * in the payload would tell it so.
 */
function opportunitySteps(opportunity: RefactoringOpportunityDetailResolved): string {
  const byId = new Map(opportunity.plans.map((plan) => [plan.id, plan]));
  return opportunity.steps
    .map((step, i) => {
      const plan = byId.get(step.plan_id);
      const meta = typeMeta(step.refactoring_type);
      const lines: (string | null)[] = [
        `${i + 1}. **${meta.label}** — ${planSourceLink(step.file_path, step.line_start, step.line_end)}${
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
    })
    .join("\n\n");
}

/** Indent a multi-line block so it reads as the body of its numbered step. */
function indentBlock(body: string, pad: string): string {
  return body
    .split("\n")
    .map((line) => (line.trim() === "" ? "" : `${pad}${line}`))
    .join("\n");
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
  return [
    "## Evidence behind the diagnosis",
    "",
    "Supporting observations, not extra work. They are why the diagnosis reads the way it does.",
    "",
    rows.join("\n"),
    opportunity.evidence_truncated
      ? `\n${opportunity.evidence_emitted} of ${opportunity.evidence_total} shown.`
      : "",
  ]
    .filter((s) => s !== "")
    .join("\n");
}

/** The validation profiles the steps share, with their runnable commands. */
function opportunityValidation(opportunity: RefactoringOpportunityDetailResolved): string {
  if (opportunity.validation_profiles.length === 0) return "";
  const blocks = opportunity.validation_profiles.map((profile) => {
    const evidence =
      profile.basis === "unknown"
        ? "No measured or inferred guarding test was found; treat this as a validation gap."
        : `${profile.total} guarding test${profile.total === 1 ? "" : "s"} via ${
            profile.via ?? profile.basis
          }${profile.truncated ? ` (showing ${profile.tests.length})` : ""}.`;
    return bulletList([
      evidence,
      profile.tests.length
        ? `Tests: ${profile.tests.map((test) => `\`${test}\``).join(", ")}.`
        : null,
      profile.commands.length
        ? `Run: ${profile.commands.map((command) => `\`${command}\``).join("; ")}.`
        : null,
    ]);
  });
  return ["## Validation plan", "", blocks.join("\n")].join("\n");
}

/**
 * The line that tells an MCP-capable agent it can pull this record itself.
 *
 * An id in a prompt with no call that resolves it is noise — the work-queue
 * prompt prints `- Target: <id>` and nothing accepts it, which is the mistake
 * this deliberately does not repeat. So the id is stated for every flavor,
 * because it is how a person reports completion and how staleness is detected,
 * but only the MCP flavor is told to call anything with it. Every other flavor
 * gets the whole plan inlined above and is told plainly that it cannot query
 * back, rather than being handed a tool name it has no way to invoke.
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

/**
 * Build a ready-to-paste prompt that hands a coding agent ONE composed
 * refactoring opportunity: the file, what it leads with, its ordered steps with
 * the mechanical/judgment split, the evidence, the validation commands, and the
 * stable id that resolves back to the record.
 *
 * This is the opportunity-level sibling of `buildRefactoringPlanPrompt`, which
 * still handles a single step.
 */
export function buildRefactoringOpportunityPrompt({
  opportunity,
  flavor = "generic",
  repoName,
}: BuildRefactoringOpportunityPromptOptions): string {
  const repoLine = repoName ? ` (\`${repoName}\`)` : "";
  const meta = typeMeta(opportunity.lead_refactoring_type || "");
  const others = opportunity.affected_files.filter((f) => f !== opportunity.file_path);

  // Tri-state, and all three states survive. `null` means no dominant finding
  // was recorded to compare against, which is not the claim `false` makes.
  const primaryLine =
    opportunity.addresses_primary_problem === true
      ? "These steps address the file's dominant diagnosed problem."
      : opportunity.addresses_primary_problem === false
        ? "These steps do NOT address the file's dominant diagnosed problem — they are real work, but not the biggest cost in this file. Say so if you think something else should come first."
        : "No dominant problem was recorded for this file, so whether these steps address the main one is unknown — not answered either way.";

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
    others.length > 0
      ? `Keep these co-affected files consistent: ${others.map((f) => `\`${f}\``).join(", ")}.`
      : null,
  ];

  const completionContract = [
    "1. The refactored code, with each step above applied in order.",
    "2. Per step: whether you applied it, and for anything you skipped, the reason.",
    "3. A short note on what you renamed or introduced and why.",
    "4. Confirmation the tests pass (or the exact failures if they don't).",
    `5. The opportunity id \`${opportunity.opportunity_id}\`, so the work can be matched back to the record.`,
  ];

  return [
    FLAVOR_PREAMBLE[flavor],
    "",
    `## ${meta.label} in \`${opportunity.file_path}\`${repoLine}`,
    "",
    bulletList([
      `Opportunity: \`${opportunity.opportunity_id}\``,
      `File: \`${opportunity.file_path}\``,
      opportunity.lead_biomarker
        ? `Leading cause: ${opportunity.lead_biomarker.replace(/_/g, " ")}.`
        : null,
      `${opportunity.step_count} step${opportunity.step_count === 1 ? "" : "s"}: ${opportunity.mechanical_steps} mechanical, ${opportunity.judgment_steps} judgment.`,
      opportunity.recoverable_health > 0
        ? `Recovers ~${opportunity.recoverable_health.toFixed(2)} of health score if applied.`
        : null,
      `Effort: ${opportunity.effort_bucket} bucket. Detector confidence: ${opportunity.confidence}.`,
      primaryLine,
    ]),
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
    "## Hard constraints",
    "",
    bulletList(constraintList),
    "",
    "## What I expect back",
    "",
    completionContract.join("\n"),
    "",
    opportunityHandoff(opportunity, flavor),
    "",
    explorationCloser(flavor, opportunity.file_path, "refactor"),
  ]
    .filter((s) => s !== "")
    .join("\n");
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
  const repoLine = repoName ? ` (\`${repoName}\`)` : "";
  // Triaged findings stay on the row. A prompt that asked an agent to fix
  // something already marked resolved would be reporting the drawer's state
  // rather than the file's.
  const open = findings.filter(
    (f) => f.status !== "resolved" && f.status !== "false_positive",
  );
  const ranked = open.slice().sort((a, b) => b.health_impact - a.health_impact);
  const detailed = ranked.slice(0, MAX_FILE_HEALTH_FINDINGS);
  const remainder = ranked.slice(MAX_FILE_HEALTH_FINDINGS);

  const pillars = bulletList([
    file.defect_score != null ? `Defect risk: **${file.defect_score.toFixed(1)}/10**` : null,
    file.maintainability_score != null
      ? `Maintainability: **${file.maintainability_score.toFixed(1)}/10**`
      : null,
    file.performance_score != null
      ? `Performance: **${file.performance_score.toFixed(1)}/10**`
      : null,
  ]);

  const snapshot = bulletList([
    `Health score: **${file.score.toFixed(1)}/10** (lower is worse; 10.0 is clean)`,
    file.total_deduction != null
      ? `Total deduction: **−${file.total_deduction.toFixed(2)}** across ${categories.length} scoring ${
          categories.length === 1 ? "category" : "categories"
        }`
      : null,
    trendDelta != null && trendDelta !== 0
      ? `Score moved ${trendDelta > 0 ? "up" : "down"} ${Math.abs(trendDelta).toFixed(2)} since the previous snapshot`
      : null,
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

  const categoryBlock =
    categories.length > 0
      ? bulletList(
          categories
            .slice()
            .sort((a, b) => b.applied_deduction - a.applied_deduction)
            .map((c) => {
              const label =
                CATEGORY_LABEL[c.category as keyof typeof CATEGORY_LABEL] ?? c.category;
              const cap = c.cap != null ? `, capped at −${c.cap.toFixed(1)}` : "";
              // A capped category understates itself: the raw deductions ran
              // past the ceiling, so this figure is a floor on how bad it is,
              // not a measurement of it.
              const note = c.capped
                ? " — **at its ceiling**, so this understates the category"
                : "";
              return `${label}: −${c.applied_deduction.toFixed(2)} from ${c.finding_count} finding${
                c.finding_count === 1 ? "" : "s"
              }${cap}${note}`;
            }),
        )
      : null;

  const findingsBlock = detailed
    .map((f, i) => {
      const info = biomarkerInfo(f.biomarker_type);
      const loc = f.function_name
        ? `function \`${f.function_name}\`${
            f.line_start ? ` (line ${f.line_start}${f.line_end ? `–${f.line_end}` : ""})` : ""
          }`
        : "file-level";
      const extra = biomarkerExtraContext(f.biomarker_type, f.details);
      const suggestion = suggestions[f.biomarker_type];
      return [
        `${i + 1}. **${info.label}** · ${CATEGORY_LABEL[info.category]} · ${f.severity.toUpperCase()} · health impact −${f.health_impact.toFixed(2)}`,
        `   - Where: ${loc}`,
        `   - Why it's a problem: ${info.description}`,
        `   - Observed: ${f.reason}`,
        extra ? `   - Extra context: ${extra}` : null,
        suggestion ? `   - Suggested direction: ${suggestion}` : null,
      ]
        .filter(Boolean)
        .join("\n");
    })
    .join("\n\n");

  const remainderLine = (() => {
    if (remainder.length === 0) return null;
    const counts = new Map<string, number>();
    for (const f of remainder) {
      counts.set(f.biomarker_type, (counts.get(f.biomarker_type) ?? 0) + 1);
    }
    const grouped = Array.from(counts.entries())
      .sort((a, b) => b[1] - a[1])
      .map(([type, n]) => `${n}× ${biomarkerInfo(type).label}`)
      .join(", ");
    const tail = remainder.reduce((s, f) => s + f.health_impact, 0);
    return `…and ${remainder.length} more lower-impact finding${
      remainder.length === 1 ? "" : "s"
    } (${grouped}; −${tail.toFixed(2)} total). Handle these only after the ranked items above.`;
  })();

  const signalLines = signals
    ? bulletList([
        signals.commit_count_90d != null
          ? `${signals.commit_count_90d} commits in the last 90 days`
          : null,
        signals.change_entropy_pct != null
          ? `Change entropy ${Math.round(signals.change_entropy_pct)}%, which is how scattered those edits were across the file`
          : null,
        signals.prior_defect_count != null && signals.prior_defect_count > 0
          ? `${signals.prior_defect_count} prior bug fixes have landed here${
              signals.last_fix_at ? `, most recently ${signals.last_fix_at.slice(0, 10)}` : ""
            }`
          : null,
        signals.bug_magnet
          ? "Flagged a **bug magnet**: fixes keep landing in this file, so treat a regression here as likely rather than unlucky."
          : null,
        signals.in_degree != null
          ? `${signals.in_degree} file${signals.in_degree === 1 ? "" : "s"} import this one, so changing its public surface reaches all of them`
          : null,
        signals.out_degree != null ? `It imports ${signals.out_degree} others` : null,
        signals.age_days != null ? `First seen ${signals.age_days} days ago` : null,
      ])
    : null;

  const causes = performance?.items ?? [];
  const causeBlock =
    causes.length > 0
      ? [
          bulletList(
            causes.slice(0, MAX_FILE_HEALTH_CAUSES).map((c) => {
              // Titled the way every other surface titles a cause: the marker
              // plus where it fires. The fix's strategy name is not the
              // cause's name, and using it made distinct causes read as
              // several copies of one.
              const first = c.evidence[0];
              const symbol = c.intervention_symbol ?? first?.function_name ?? null;
              const at = symbol ? ` in \`${symbol}\`` : "";
              const line = first?.line_start != null ? ` (line ${first.line_start})` : "";
              const reach =
                c.affected_call_sites_total > 1
                  ? ` Reaches ${c.affected_call_sites_total} call sites across ${c.affected_files_total} file${
                      c.affected_files_total === 1 ? "" : "s"
                    }.`
                  : "";
              const fix = c.fix
                ? ` Recorded fix: ${c.fix.strategy} (${c.fix.safety}). ${c.fix.rationale}`
                : "";
              return `**${biomarkerInfo(c.biomarker_type).label}**${at}${line} — ${c.actionability_reason}${reach}${fix}`;
            }),
          ),
          causes.length > MAX_FILE_HEALTH_CAUSES
            ? `…and ${causes.length - MAX_FILE_HEALTH_CAUSES} more open on this file.`
            : null,
          "These are static reads of the code, not measurements of a running system. Confirm a path is actually hot before optimising it.",
        ]
          .filter(Boolean)
          .join("\n\n")
      : null;

  const constraints = [
    "**Read first, edit second.** Read the file, its callers, its tests, and any obvious helpers before proposing a change.",
    "Group the findings by root cause before you start. Several markers on one function are usually one problem, and fixing them separately produces churn without improving the file.",
    "Preserve runtime behaviour. No new features, and no opportunistic rewrites in unrelated regions.",
    "Do **not** change public signatures or exported names unless a verified finding requires it. Call it out explicitly if you must.",
    "Keep test coverage at least as high as before. If you change logic, add or update tests.",
    "Match the existing style of the file and its neighbours.",
    "If a finding turns out to be a false positive once you have read the code, skip it and say why. That is a useful answer, not a failure.",
  ];

  const expected = [
    "1. A short triage: which findings share a root cause, which are independent, and which you believe are false positives.",
    "2. A plan of 3 to 6 bullets for the ones worth acting on, ordered so the riskiest change is not the first.",
    "3. The edits, scoped to this file plus its tests and any tightly coupled helper.",
    "4. A summary of what changed, and which specific findings each change should clear.",
  ];

  return [
    FLAVOR_PREAMBLE[flavor],
    `\n## Target file${repoLine}\n`,
    `\`${file.file_path}\``,
    `\n## Current health snapshot\n`,
    snapshot,
    pillars ? `\n### Scored dimensions\n\n${pillars}` : "",
    file.primary_biomarker
      ? `\n### Leading cause\n\n**${biomarkerInfo(file.primary_biomarker).label}.**${
          file.primary_reason ? ` ${file.primary_reason}` : ""
        }`
      : "",
    categoryBlock ? `\n## Where the deduction comes from\n\n${categoryBlock}` : "",
    detailed.length > 0 ? `\n## Open findings (ranked by impact)\n\n${findingsBlock}` : "",
    remainderLine ? `\n${remainderLine}` : "",
    causeBlock ? `\n## Open performance causes\n\n${causeBlock}` : "",
    signalLines ? `\n## How this file behaves over time\n\n${signalLines}` : "",
    // These carry their own leading blank line, because the filter below that
    // drops absent sections also drops any bare "" used as a separator.
    `\n## Hard constraints\n`,
    bulletList(constraints),
    `\n## What I expect back\n`,
    expected.join("\n"),
    `\n${explorationCloser(flavor, file.file_path, "file-health")}`,
  ]
    .filter((s) => s !== "")
    .join("\n");
}
