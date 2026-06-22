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
type CloserKind = "refactor" | "coverage" | "security" | "hotspot";

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
    explorationCloser(flavor, row.file_path, "coverage"),
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
        risk.length ? `  - Runtime-load risk to rule out first: ${risk.join(", ")}` : null,
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
