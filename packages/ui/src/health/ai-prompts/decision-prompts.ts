import {
  bulletList,
  closingSections,
  FLAVOR_PREAMBLE,
  joinSections,
  repoSuffix,
  type AiPromptFlavor,
} from "./shared";

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

/** Governed paths listed by name before the rest are counted. */
const MAX_GOVERNED_LISTED = 8;
/** Governed paths handed to `get_context` in the MCP closer. */
const MAX_GOVERNED_QUERIED = 3;

const ENFORCE_EXPECTED = [
  "1. A conformance audit: each governed file/module and whether it conforms or violates the decision, with evidence.",
  "2. For every violation, the concrete fix (file, symbol, and the change), ordered by impact.",
  "3. The fixes applied (or, if you can't safely change something, why, and what a human should do).",
  "4. Anything governed by the decision that you couldn't check, so nothing is silently skipped.",
];

/** Every module and file the decision is recorded as governing. */
function governedScope(d: DecisionPromptInput): string[] {
  return [...(d.affected_modules ?? []), ...(d.affected_files ?? [])];
}

function governsLine(scope: string[]): string | null {
  if (scope.length === 0) return null;
  const listed = scope.slice(0, MAX_GOVERNED_LISTED).map((s) => `\`${s}\``).join(", ");
  const more =
    scope.length > MAX_GOVERNED_LISTED ? `, +${scope.length - MAX_GOVERNED_LISTED} more` : "";
  return `Governs: ${listed}${more}`;
}

function textSection(heading: string, body: string | null | undefined): string {
  return body ? [heading, "", body, ""].join("\n") : "";
}

function listSection(heading: string, items: string[] | undefined): string {
  return items && items.length > 0 ? [heading, "", bulletList(items), ""].join("\n") : "";
}

/**
 * The opening of the MCP closer both decision prompts share: which tool
 * serves the rationale and which serves the governed code.
 */
function decisionToolsLead(d: DecisionPromptInput, scope: string[]): string {
  const paths = scope
    .slice(0, MAX_GOVERNED_QUERIED)
    .map((s) => `'${s}'`)
    .join(", ");
  return `Use \`get_why('${d.title.replace(/'/g, "")}')\` for the recorded rationale and \`get_context([${paths}])\` for the governed code — repowise links decisions to graph nodes, so`;
}

function verificationTask(isProposed: boolean, isStale: boolean): string {
  if (isProposed) {
    return "repowise auto-proposed this architectural decision from the code and history. Verify it: does the codebase actually reflect this decision today? Then recommend whether to **confirm** it (it's real and current) or **reject** it (it's wrong, speculative, or already superseded).";
  }
  if (isStale) {
    return "This recorded decision is flagged stale — the code it governs has changed since it was written. Re-verify it against the current code and recommend whether to **keep**, **update**, or **deprecate** it.";
  }
  return "Verify this recorded decision against the current code: is it still honored in the implementation? Recommend whether to keep, update, or deprecate it.";
}

function verificationExpected(isProposed: boolean): string[] {
  return [
    `1. A verdict: ${isProposed ? "**confirm** or **reject**" : "**keep**, **update**, or **deprecate**"}, in one line.`,
    "2. The evidence: the files/symbols you checked and whether each conforms.",
    "3. Any conformance gaps — places that violate the decision — as a short list.",
    "4. If you'd update the decision text, the exact wording you'd change.",
  ];
}

export function buildDecisionAiPrompt({
  decision: d,
  flavor = "generic",
  repoName,
}: BuildDecisionPromptOptions): string {
  const isProposed = d.status === "proposed";
  const isStale = (d.staleness_score ?? 0) > 0.5;
  const scope = governedScope(d);

  const constraints = [
    "Ground every claim in the actual code, not the decision text. The decision describes intent; the code is the truth. Where they disagree, the code wins and the decision is stale.",
    scope.length > 0
      ? "Start from the affected modules/files listed below, then follow the dependency graph to anything that should obey this decision but doesn't."
      : "Identify which parts of the codebase this decision governs, then check them for conformance.",
    "Cite specific files/symbols as evidence for your verdict — don't assert without a reference.",
    "Distinguish 'the decision is wrong' from 'the code drifted from a still-good decision' — they lead to opposite actions (reject/deprecate vs. fix the code).",
    "Do not change code as part of this task unless asked — this is a verification, not an implementation.",
  ];

  const closer =
    flavor === "claude-code-mcp"
      ? `${decisionToolsLead(d, scope)} verify against that instead of guessing. Then check conformance file by file.`
      : "Read the decision below, then open the code it governs and check it line up. The decision is a claim about the code — your job is to confirm or refute it with evidence.";

  return joinSections([
    FLAVOR_PREAMBLE[flavor],
    "",
    `## Architectural decision to verify${repoSuffix(repoName)}`,
    "",
    bulletList([
      `Title: **${d.title}**`,
      `Status: ${d.status}`,
      d.confidence != null ? `Recorded confidence: ${Math.round(d.confidence * 100)}%` : null,
      isStale ? `Staleness: ${(d.staleness_score ?? 0).toFixed(2)} — flagged stale` : null,
      governsLine(scope),
    ]),
    "",
    textSection("## Context", d.context),
    textSection("## Decision", d.decision),
    textSection("## Rationale", d.rationale),
    listSection("## Alternatives rejected", d.alternatives),
    listSection("## Consequences", d.consequences),
    "## Your task",
    "",
    verificationTask(isProposed, isStale),
    "",
    ...closingSections(constraints, verificationExpected(isProposed)),
    closer,
  ]);
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
  const scope = governedScope(d);

  const constraints = [
    "Treat the decision text below as the standard; your job is conformance, not re-litigating whether the decision is right. If you find strong evidence the decision itself is wrong, stop and report that instead of enforcing it.",
    scope.length > 0
      ? "Start from the affected modules/files listed below, then follow the dependency graph to anything else that should obey this decision."
      : "First identify which parts of the codebase this decision governs, then audit them.",
    "Cite specific files/symbols for every violation — don't assert without a reference.",
    "Propose the minimal change that brings each violation into conformance; don't refactor beyond what the decision requires.",
    "Keep behavior identical except where the decision explicitly requires otherwise; call out any user-visible change.",
  ];

  const closer =
    flavor === "claude-code-mcp"
      ? `${decisionToolsLead(d, scope)} audit against that instead of guessing.`
      : "Read the decision below, open the code it governs, and make the code match the decision — with a cited audit trail for every change.";

  return joinSections([
    FLAVOR_PREAMBLE[flavor],
    "",
    `## Architectural decision to enforce${repoSuffix(repoName)}`,
    "",
    bulletList([`Title: **${d.title}**`, `Status: ${d.status}`, governsLine(scope)]),
    "",
    textSection("## Context", d.context),
    textSection("## Decision", d.decision),
    textSection("## Rationale", d.rationale),
    listSection("## Consequences", d.consequences),
    "## Your task",
    "",
    "Audit the governed code for compliance with this decision, then fix every violation you find. This is an enforcement pass: the outcome should be code that conforms, not just a report.",
    "",
    ...closingSections(constraints, ENFORCE_EXPECTED),
    closer,
  ]);
}
