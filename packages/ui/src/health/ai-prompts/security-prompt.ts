import {
  bulletList,
  closingSections,
  explorationCloser,
  FLAVOR_PREAMBLE,
  joinSections,
  repoSuffix,
  type AiPromptFlavor,
} from "./shared";

// ─────────────────────────────────────────────────────────────────────
// Security remediation prompt (per finding)
// ─────────────────────────────────────────────────────────────────────

export interface SecurityPromptFinding {
  file_path: string;
  kind: string;
  severity: string;
  snippet?: string | null;
  line_number?: number | null;
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
  const isSecret = isSecretKind(finding.kind);

  return joinSections([
    FLAVOR_PREAMBLE[flavor],
    "",
    `## Security finding${repoSuffix(repoName)}`,
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
    ...closingSections(constraints(isSecret), contract(isSecret)),
    explorationCloser(flavor, finding.file_path, "security"),
  ]);
}

function isSecretKind(kind: string): boolean {
  return /secret|key|token|credential|password/i.test(kind);
}

function constraints(isSecret: boolean): string[] {
  return [
    "**Confirm it's real first.** Reproduce the issue or trace the data flow before editing. Pattern scanners over-flag — test fixtures, sample data, and already-sanitized paths are common false positives. If this is one, say so and stop.",
    isSecret
      ? "If this is a live secret, the fix is two-part: (1) remove it from the code and load it from a secret manager / env var, and (2) call out that the secret must be **rotated** — it is compromised the moment it lands in git history."
      : "Fix the root cause, not the symptom — validate/escape/parameterize at the boundary rather than blocking one known-bad input.",
    "Preserve behavior for legitimate inputs. Don't break the feature to silence the scanner.",
    "Add or update a test that fails on the vulnerable behavior and passes after the fix, where the project's setup allows it.",
    "Don't introduce a new dependency for this unless there's no safe stdlib/first-party option; if you do, justify it.",
  ];
}

function contract(isSecret: boolean): string[] {
  return [
    "1. A one-line verdict: is this exploitable, and how (or why it's a false positive)?",
    "2. The fix, scoped to the smallest change that closes the issue.",
    "3. The test that now covers it, if one was feasible.",
    isSecret ? "4. An explicit rotation/remediation note for the exposed secret." : "4. Any related spots in the codebase with the same pattern that should get the same fix.",
  ];
}

/** Findings listed in a batch prompt; the rest are counted, not dropped silently. */
const MAX_BATCH_FINDINGS = 40;

export interface BuildSecurityBatchPromptOptions {
  findings: SecurityPromptFinding[];
  flavor?: AiPromptFlavor;
  repoName?: string;
}

/** One prompt over several findings, e.g. every row the current filters show. */
export function buildSecurityBatchAiPrompt({
  findings,
  flavor = "generic",
  repoName,
}: BuildSecurityBatchPromptOptions): string {
  const only = findings.length === 1 ? findings[0] : undefined;
  if (only) return buildSecurityAiPrompt({ finding: only, flavor, ...(repoName ? { repoName } : {}) });
  const isSecret = findings.some((f) => isSecretKind(f.kind));
  const shown = findings.slice(0, MAX_BATCH_FINDINGS);
  const hidden = findings.length - shown.length;
  const location = (f: SecurityPromptFinding) =>
    f.line_number != null ? `${f.file_path}:${f.line_number}` : f.file_path;

  return joinSections([
    FLAVOR_PREAMBLE[flavor],
    "",
    `## Security findings${repoSuffix(repoName)}`,
    "",
    bulletList([
      `Findings: **${findings.length}**`,
      "Source: repowise local security scan (pattern-based — treat each as a lead).",
    ]),
    "",
    "## Findings, in the order to work them",
    "",
    bulletList(
      shown.map((f) => `\`${location(f)}\` — **${f.kind}** (${f.severity.toUpperCase()})`),
    ),
    hidden > 0 ? `…and ${hidden} more in the same list (open the security page in repowise for all of them).` : "",
    "",
    "## Your task",
    "",
    "Work through these findings one at a time, shipped code before tests and docs. Confirm each before changing anything, and group fixes that share a root cause.",
    "",
    ...closingSections(constraints(isSecret), contract(isSecret)),
    explorationCloser(flavor, shown[0]?.file_path ?? "", "security"),
  ]);
}
