import { bulletList, explorationCloser, FLAVOR_PREAMBLE, type AiPromptFlavor } from "./shared";

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
