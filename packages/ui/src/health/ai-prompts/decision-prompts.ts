import { bulletList, FLAVOR_PREAMBLE, type AiPromptFlavor } from "./shared";

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
