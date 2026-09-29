import type { NextAction } from "@repowise-dev/types/actions";

import {
  bulletList,
  closingSections,
  FLAVOR_PREAMBLE,
  joinSections,
  repoSuffix,
  type AiPromptFlavor,
} from "./shared";

// ─────────────────────────────────────────────────────────────────────
// Next action prompt: one action from the Overview's "Do next" list
// ─────────────────────────────────────────────────────────────────────

export interface BuildActionPromptOptions {
  action: NextAction;
  flavor?: AiPromptFlavor;
  repoName?: string;
}

const CONSTRAINTS = [
  "**Confirm the facts before you change anything.** The action was built from a stored index; read the code and its history to check that what it says still holds.",
  "**Stay inside the target.** Change what the action names. If the right fix is somewhere else, stop and say where and why instead of widening the change.",
  "**Keep behaviour unless the action is about behaviour.** Refactors and test additions must not change what the code does; run the existing tests before and after.",
  "**Say when the action is wrong.** If the facts do not hold, or the fix would cost more than the problem, report that rather than forcing an edit.",
];

const EXPECTED = [
  "1. What you checked, and whether the action's facts held.",
  "2. The change, or the reason you made none.",
  "3. How the done condition below is now met, or what is left.",
];

/**
 * Hand one next action to an agent: its claim, the evidence behind it, and
 * what finished looks like. The agent is told to verify first, because the
 * action is a lead drawn from stored analysis rather than a live reading.
 */
export function buildActionPrompt({
  action,
  flavor = "generic",
  repoName,
}: BuildActionPromptOptions): string {
  const target = action.target.symbol
    ? `\`${action.target.path}\` (\`${action.target.symbol}\`)`
    : action.target.path
      ? `\`${action.target.path}\``
      : "the repository";
  return joinSections([
    FLAVOR_PREAMBLE[flavor],
    "",
    `## Next action${repoSuffix(repoName)}`,
    "",
    `**${action.title}**`,
    "",
    action.impact,
    "",
    "## Evidence",
    "",
    bulletList([
      `Target: ${target}`,
      ...action.why.map(
        (w) => `${w.label}: ${w.value}${w.basis === "measured" ? "" : ` (${w.basis})`}`,
      ),
      action.includes.length
        ? `Covers: ${action.includes.map((p) => `\`${p}\``).join(", ")}`
        : null,
      `Effort estimate: ${action.effort}; confidence: ${action.confidence}`,
    ]),
    "",
    "## Done when",
    "",
    action.done_when + (action.command ? `\n\nCommand: \`${action.command}\`` : ""),
    "",
    ...closingSections(CONSTRAINTS, EXPECTED),
    flavor === "claude-code-mcp"
      ? "Start with `get_risk([...])` on the target for its history and test gaps, and `get_context([...])` for its structure, before reading files by hand."
      : "",
  ]);
}
