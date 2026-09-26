import {
  bulletList,
  closingSections,
  FLAVOR_PREAMBLE,
  joinSections,
  pluralS,
  repoSuffix,
  type AiPromptFlavor,
} from "./shared";

// ─────────────────────────────────────────────────────────────────────
// Conformance prompt (bulk: architecture rule violations)
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

const CONSTRAINTS = [
  "**Fix the architecture, not the rule.** The goal is to remove the disallowed dependency, not to relax the declared rule (unless the rule is genuinely wrong — if so, say so and stop).",
  "For each violation, find the actual import/call/event that creates the edge, then choose the right fix: invert the dependency, introduce a shared interface/contract module, move the shared code to an allowed layer, or route through an allowed intermediary.",
  "Preserve behavior. These are structural changes across service boundaries — keep the public contract stable.",
  "Tackle one violation (or one tightly-related cluster) per change so each is reviewable and revertible.",
  "Update or add tests that would catch the boundary regressing again.",
];

const EXPECTED = [
  "1. For each violation: the exact code that creates the disallowed edge (file + symbol).",
  "2. The chosen fix and why it's the right one (invert / interface / move / intermediary).",
  "3. The change itself, scoped per violation, with tests.",
  "4. Any violation you believe reflects a wrong rule rather than wrong code, with your reasoning.",
];

function violationEntry(v: ConformancePromptViolation, index: number): string {
  const src = v.source_name || v.source;
  const tgt = v.target_name || v.target;
  const rule = v.rule_source && v.rule_target ? `${v.rule_source} !-> ${v.rule_target}` : null;
  return [
    `${index + 1}. **${src} → ${tgt}**${v.edge_kind ? ` (${v.edge_kind})` : ""}`,
    rule ? `   - Breaks rule: \`${rule}\`` : null,
    v.rule_description ? `   - Rule intent: ${v.rule_description}` : null,
  ]
    .filter(Boolean)
    .join("\n");
}

export function buildConformanceAiPrompt({
  violations,
  flavor = "generic",
  repoName,
}: BuildConformancePromptOptions): string {
  const shown = violations.slice(0, MAX_CONFORMANCE_VIOLATIONS);
  const hidden = violations.length - shown.length;

  return joinSections([
    FLAVOR_PREAMBLE[flavor],
    "",
    `## Architecture conformance violations${repoSuffix(repoName)}`,
    "",
    bulletList([
      `Violations: **${violations.length}** dependencies that break a declared rule.`,
      "Source: repowise workspace conformance check (live dependency graph vs declared rules).",
    ]),
    "",
    "## Violations to resolve",
    "",
    shown.map(violationEntry).join("\n\n"),
    hidden > 0
      ? `\n…and ${hidden} more violation${pluralS(hidden)} — resolve these after the above.`
      : "",
    "",
    ...closingSections(CONSTRAINTS, EXPECTED),
    flavor === "claude-code-mcp"
      ? "For each violation, use `get_blast_radius` / `get_context` on the two services to find the exact edge and what else rides on it before you cut it — repowise already resolved the cross-repo graph. Don't restructure blind."
      : "For each violation, locate the real import/call/event that creates the edge before proposing a fix. The rule names the boundary; the code is where you'll actually sever it.",
  ]);
}
