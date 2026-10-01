import type { FixItem } from "@repowise-dev/types/fix-first";

import { bulletList, closingSections, joinSections, repoSuffix, type AiPromptFlavor } from "./shared";

// ─────────────────────────────────────────────────────────────────────
// Fix first: one ranked item, handed to an agent with its Verify block
// ─────────────────────────────────────────────────────────────────────

export interface BuildFixItemPromptOptions {
  item: FixItem;
  flavor?: AiPromptFlavor;
  repoName?: string;
}

const OPENING: Record<AiPromptFlavor, string> = {
  generic:
    "You are a senior engineer in this repository. Repowise, a code-intelligence index, ranked the change below first among the work it found. Its facts come from stored analysis; treat them as leads to confirm against the code, not as ground truth.",
  "claude-code":
    "You are Claude Code in this repository. Repowise, a code-intelligence index, ranked the change below first among the work it found. Confirm each fact with Read and Grep before editing, and track the steps with TodoWrite.",
  "claude-code-mcp":
    "You are Claude Code in this repository, which Repowise indexes and serves over MCP. Repowise ranked the change below first among the work it found. Call the MCP lookup under \"Look closer\" for the full record before reading files by hand, then confirm each fact against the code.",
  cursor:
    "Work in this repository. Repowise, a code-intelligence index, ranked the change below first among the work it found. Open the target with @file, confirm each fact, then make the change.",
};

const CONSTRAINTS = [
  "**Confirm before changing.** The analysis is stored, not live; the code may have moved since it ran.",
  "**Keep behaviour.** This is a structural or performance change; what the code returns must not change.",
  "**Run Verify before and after.** If no guarding test exists, write one that pins today's behaviour first.",
  "**Say when it is wrong.** If the problem does not hold, or the change costs more than it saves, report that and stop.",
];

const EXPECTED = [
  "1. Whether the problem held, and what you changed.",
  "2. The Verify run, before and after, with its result.",
  "3. Anything you left undone, and why.",
];

function where(item: FixItem): string {
  const { file_path, symbol, line_start } = item.target;
  const loc = `\`${file_path}${line_start ? `:${line_start}` : ""}\``;
  return symbol ? `${loc} (\`${symbol.split("::").pop()}\`)` : loc;
}

/** The Verify block, worded the way the card words it. */
export function fixVerifyLines(item: FixItem): string {
  const { tests, tests_total, command } = item.verify;
  if (tests.length === 0) {
    return "No guarding tests found. Write a test that pins the current behaviour before you change anything.";
  }
  const listed = tests.map((t) => `- \`${t.path}\`${t.reason ? `: ${t.reason}` : ""}`);
  const more = tests_total - tests.length;
  return joinSections([
    listed.join("\n"),
    more > 0 ? `- …and ${more.toLocaleString()} more` : "",
    command ? `\nRun: \`${command}\`` : "",
  ]);
}

/**
 * Hand one Fix-first item to an agent: the change, why it ranks, the steps in
 * order, and how to verify it. Written so an agent with no prior context can
 * start from this text alone.
 */
export function buildFixItemPrompt({
  item,
  flavor = "generic",
  repoName,
}: BuildFixItemPromptOptions): string {
  const useMcp = flavor === "claude-code-mcp";
  const call = item.next_call.mcp;
  const steps = item.action.steps.map(
    (s) =>
      `${s.order}. ${s.text} (\`${s.file_path}${s.line ? `:${s.line}` : ""}\`)${
        s.mechanical ? " [mechanical]" : " [judgment]"
      }`,
  );
  const moreSteps = item.action.steps_total - item.action.steps.length;

  return joinSections([
    OPENING[flavor],
    "",
    `## Fix first${repoSuffix(repoName)}`,
    "",
    `**${item.title}**`,
    "",
    item.why,
    "",
    "## Facts",
    "",
    bulletList([
      `Target: ${where(item)}`,
      `Gain: ${item.gain.text}`,
      `Effort: ${item.effort.bucket} (${item.effort.basis})`,
      `Risk: ${item.risk.text}`,
      `Confidence: ${item.confidence.level}. ${item.confidence.reason}`,
      ...item.facts.map((f) => `${f.label}: ${f.value}${f.basis === "measured" ? "" : ` (${f.basis})`}`),
    ]),
    "",
    steps.length
      ? joinSections([
          "## Steps, in order",
          "",
          steps.join("\n"),
          moreSteps > 0 ? `…and ${moreSteps.toLocaleString()} more; the lookup below returns them.` : "",
          "",
        ])
      : "",
    "## Verify",
    "",
    fixVerifyLines(item),
    "",
    useMcp ? joinSections(["## Look closer (Repowise MCP)", "", `\`${call}\``, ""]) : "",
    ...closingSections(CONSTRAINTS, EXPECTED),
  ]);
}
