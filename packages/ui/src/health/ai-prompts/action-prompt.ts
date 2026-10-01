import type { ActionDetail, NextAction } from "@repowise-dev/types/actions";

import { biomarkerLabel } from "../biomarker-glossary";
import {
  bulletList,
  closingSections,
  joinSections,
  preamble,
  repoSuffix,
  type AiPromptFlavor,
} from "./shared";

// ─────────────────────────────────────────────────────────────────────
// Next action prompt: one action from "Do next", handed to an agent cold
// ─────────────────────────────────────────────────────────────────────

export interface BuildActionPromptOptions {
  action: NextAction;
  flavor?: AiPromptFlavor;
  repoName?: string;
}

/**
 * The opening after the shared role. Not the file preamble: an action can span
 * many files and is built from stored analysis, so the agent is told both
 * before it reads a line.
 */
const OPENING: Record<AiPromptFlavor, string> = {
  generic:
    "Repowise, a code-intelligence index, produced the action below from its stored analysis of the repository (git history, code health, the dependency graph). Its evidence is listed in full or in part; treat each item as a lead to verify against the code, not as ground truth.",
  "claude-code":
    "Repowise, a code-intelligence index, produced the action below from its stored analysis. Treat each evidence item as a lead: read the code with Read and Grep, confirm it, and use TodoWrite to track the files you work through.",
  "claude-code-mcp":
    "The action below comes from Repowise's stored analysis. Use the MCP calls listed under \"Look closer\" before reading files by hand: they return the full evidence with line numbers, reasons and history. Treat each item as a lead to confirm against the code.",
  cursor:
    "Repowise, a code-intelligence index, produced the action below from its stored analysis. Treat each evidence item as a lead: open the file with @file, confirm it, then act.",
};

const CONSTRAINTS = [
  "**Confirm each item before changing anything.** The analysis is stored, not live; the code may have moved since it ran.",
  "**Stay inside what the action names.** Work only on the files and functions listed. If the right fix is elsewhere, stop and say where and why.",
  "**Keep behaviour unless the action is about behaviour.** Refactors and test additions must not change what the code does; run the tests that cover each file before and after.",
  "**Say when an item is wrong.** If a finding does not hold, or its fix would cost more than it saves, report that rather than forcing an edit.",
];

const EXPECTED = [
  "1. For each item you looked at: whether it held, and what you changed or why you left it.",
  "2. The tests you ran, before and after.",
  "3. What remains against the done condition.",
];

function detailLine(d: ActionDetail): string {
  const where = `\`${d.path}${d.line ? `:${d.line}` : ""}\``;
  const what = [
    d.symbol ? `\`${d.symbol.split("::").pop()}\`` : null,
    d.marker ? biomarkerLabel(d.marker) : null,
    d.severity,
  ]
    .filter(Boolean)
    .join(", ");
  const ref = d.ref && /^[0-9a-f]{7,40}$/.test(d.ref) ? ` (commit ${d.ref.slice(0, 10)})` : "";
  return `- ${where}${what ? `: ${what}` : ""}${ref}${d.reason ? `\n  - ${d.reason}` : ""}`;
}

/**
 * Hand one next action to an agent: the claim, the evidence itself, how to get
 * the rest, and what finished looks like. Written so an agent with no prior
 * context can start work from this text alone.
 */
export function buildActionPrompt({
  action,
  flavor = "generic",
  repoName,
}: BuildActionPromptOptions): string {
  const useMcp = flavor === "claude-code-mcp";
  const shownDetails = action.details.length;
  const moreDetails = action.details_total - shownDetails;
  const target = action.target.symbol
    ? `\`${action.target.path}\` (\`${action.target.symbol.split("::").pop()}\`)`
    : action.target.path
      ? `\`${action.target.path}\``
      : null;

  const commands = action.commands
    .map((c) => {
      const line = useMcp ? (c.mcp ?? c.cli) : (c.cli ?? c.mcp);
      return line ? `- ${c.purpose}:\n  \`${line}\`` : null;
    })
    .filter((l): l is string => l !== null);

  return joinSections([
    preamble(flavor, { body: OPENING }),
    "",
    `## Action${repoSuffix(repoName)}`,
    "",
    `**${action.title}**`,
    "",
    action.impact,
    "",
    "## Why it is on the list",
    "",
    bulletList([
      target ? `Target: ${target}` : null,
      ...action.why.map(
        (w) => `${w.label}: ${w.value}${w.basis === "measured" ? "" : ` (${w.basis})`}`,
      ),
      `Effort estimate: ${action.effort}; confidence in the facts: ${action.confidence}`,
    ]),
    "",
    shownDetails
      ? joinSections([
          `## Evidence (${shownDetails.toLocaleString()} of ${action.details_total.toLocaleString()}${
            moreDetails > 0 ? ", worst first" : ""
          })`,
          "",
          action.details.map(detailLine).join("\n"),
          moreDetails > 0
            ? `\n…and ${moreDetails.toLocaleString()} more. The first call under "Look closer" returns them.`
            : "",
          "",
        ])
      : action.includes.length
        ? joinSections([
            "## Files",
            "",
            action.includes.map((p) => `- \`${p}\``).join("\n"),
            "",
          ])
        : "",
    commands.length
      ? joinSections([
          useMcp ? "## Look closer (Repowise MCP)" : "## Look closer (Repowise CLI)",
          "",
          commands.join("\n"),
          "",
        ])
      : "",
    "## Done when",
    "",
    action.done_when + (action.command ? `\n\nCommand: \`${action.command}\`` : ""),
    "",
    ...closingSections(CONSTRAINTS, EXPECTED),
  ]);
}
