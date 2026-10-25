import { bulletList, REPO_ROLE, repoSuffix, type AiPromptFlavor } from "./shared";

// ─────────────────────────────────────────────────────────────────────
// Setup prompt: one Setup page row, handed to the agent to finish or fix
// ─────────────────────────────────────────────────────────────────────

export interface SetupPromptRow {
  /** The repository, as the dashboard names it. */
  repoName: string;
  /** The row's title, e.g. "Keep the index in sync". */
  title: string;
  /** What repowise detected, in a sentence or two. */
  state: string;
  /** The exact command that makes the change. */
  command: string;
  /** How the agent checks the result. */
  verify: "status" | "doctor";
  /** Something is wrong: the agent investigates and confirms before changing anything. */
  fix?: boolean;
  /** Paths or names the state refers to, listed under it. */
  items?: string[];
}

const VERIFY: Record<SetupPromptRow["verify"], string> = {
  status: "Run `repowise status` afterwards and tell me what it reports for this repository.",
  doctor:
    "Run `repowise doctor --format json` afterwards and tell me the level and detail of the matching check.",
};

const MAX_ITEMS = 20;

function itemLines(items: string[] | undefined): string | null {
  if (!items?.length) return null;
  const shown = items.slice(0, MAX_ITEMS).map((item) => `\`${item}\``);
  const more = items.length - shown.length;
  return bulletList([...shown, more > 0 ? `and ${more} more` : null]);
}

/**
 * A prompt for one Setup row: the repo, what was detected, the exact command
 * and how to verify it. A fix asks the agent to find the cause and confirm
 * with the person before changing anything.
 */
export function buildSetupAiPrompt(
  row: SetupPromptRow,
  flavor: AiPromptFlavor = "generic",
): string {
  const task = row.fix
    ? "Find out why first, then tell me what you found and what you would change. The usual fix is this command, run from the repository root:"
    : "Run this from the repository root:";
  const constraints = row.fix
    ? [
        "Confirm with me before changing anything: files, config, hooks or the index.",
        "If the problem is not what repowise reported, say so and stop.",
      ]
    : [
        "Run only the command above and the check below. Do not edit other files.",
        "If the command fails, show me its output and stop.",
      ];
  const expected = row.fix
    ? "What you found, the change you propose, and, once I confirm and you run it, what the check reports."
    : "The command's result and what the check reports.";
  const detected = [`What repowise detected: ${row.state}`, itemLines(row.items)]
    .filter(Boolean)
    .join("\n");

  // Blocks joined by a blank line, so the prompt reads as sections when pasted.
  return [
    REPO_ROLE[flavor],
    `## Setup: ${row.title}${repoSuffix(row.repoName)}`,
    detected,
    "## Do this",
    `${task}\n\n\`\`\`\n${row.command}\n\`\`\``,
    "## Verify",
    VERIFY[row.verify],
    "## Hard constraints",
    bulletList(constraints),
    "## What I expect back",
    expected,
  ].join("\n\n");
}
