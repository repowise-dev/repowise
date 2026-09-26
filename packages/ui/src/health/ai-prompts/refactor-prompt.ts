import { biomarkerInfo, CATEGORY_LABEL, splitByOrigin } from "../biomarker-glossary";
import type { HealthWorkItem } from "../refactoring-card";
import { biomarkerExtraContext, historyContextBlock } from "./findings";
import { bulletList, explorationCloser, FLAVOR_PREAMBLE, type AiPromptFlavor } from "./shared";

// ─────────────────────────────────────────────────────────────────────
// Refactor prompt
// ─────────────────────────────────────────────────────────────────────

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

  const allFindings = (
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

  // History markers are scored but unfixable, so they belong in context, not
  // in a list titled "issues to fix". The split preserves the ranking above.
  const { codeShape: fixable, history: historyFindings } = splitByOrigin(allFindings);
  const historyBlock = historyContextBlock(historyFindings);

  // Cap the detailed findings so a file with dozens of hits doesn't produce a
  // multi-thousand-token prompt. The top findings (by impact) are spelled out
  // in full; the long tail is rolled up into a single grouped line so the agent
  // still knows what's left without paying for every description.
  const MAX_DETAILED_FINDINGS = 8;
  const detailed = fixable.slice(0, MAX_DETAILED_FINDINGS);
  const remainder = fixable.slice(MAX_DETAILED_FINDINGS);

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
    detailed.length > 0
      ? ["## Issues to fix (ranked by impact)", "", findingsBlock, remainderLine ?? ""].join("\n")
      : "## Issues to fix\n\nNothing in this file's own code is currently scored. Its deduction is entirely history, listed below; there is no structural work to do here.",
    "",
    historyBlock ? ["## How this file behaves over time", "", historyBlock, ""].join("\n") : "",
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
