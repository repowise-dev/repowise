import { bulletList, REPO_ROLE, type AiPromptFlavor } from "./shared";

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
  /** The exact command that makes the change; omitted when no one command fixes it. */
  command?: string;
  /** How the agent checks the result. */
  verify: "status" | "doctor";
  /** Something is wrong: the agent says what the fix changes and waits for a yes. */
  fix?: boolean;
  /** Paths or names the state refers to, listed under it. */
  items?: string[];
  /** The finding is a `repowise doctor` check, so the prompt opens on it. */
  fromDoctor?: boolean;
}

const VERIFY: Record<SetupPromptRow["verify"], string> = {
  status: "repowise status",
  doctor: "repowise doctor",
};

const MAX_ITEMS = 20;

function itemLines(items: string[] | undefined): string | null {
  if (!items?.length) return null;
  const shown = items.slice(0, MAX_ITEMS).map((item) => `\`${item}\``);
  const more = items.length - shown.length;
  return bulletList([...shown, more > 0 ? `and ${more} more` : null]);
}

/** A sentence that ends in exactly one full stop. */
function sentence(text: string): string {
  const trimmed = text.trim().replace(/[.\s]+$/, "");
  return trimmed ? `${trimmed}.` : "";
}

/** What was found: a doctor check reads "title, detail"; a Setup row "title. state". */
function opening(row: SetupPromptRow): string {
  if (row.fromDoctor) {
    const detail = row.state.trim() ? `, ${row.state.trim()}` : "";
    return sentence(`\`repowise doctor\` in ${row.repoName} reports: ${row.title}${detail}`);
  }
  return `repowise reports for ${row.repoName}: ${sentence(row.title)} ${sentence(row.state)}`.trim();
}

/** What to do about it, worded for whether there is a fix and a command. */
function ask(row: SetupPromptRow): string {
  const check = `\`${VERIFY[row.verify]}\``;
  const again = row.fromDoctor && row.verify === "doctor" ? " again" : "";
  if (row.fix && row.command) {
    return `The fix is \`${row.command}\`. Tell me what it changes, run it once I agree, then run ${check}${again} to check.`;
  }
  if (row.fix) {
    return `No single command fixes this. Find the cause and tell me what you would change, make the change once I agree, then run ${check}${again} to check.`;
  }
  if (row.command) {
    return `Run \`${row.command}\` from the repository root, then run ${check} and tell me what it reports.`;
  }
  return `Run ${check} and tell me what it reports.`;
}

/**
 * A prompt for one Setup row: what repowise found in the repo, the command
 * that fixes it, and how to check. A fix asks the agent to say what the
 * command changes and to run it once the person agrees.
 */
export function buildSetupAiPrompt(
  row: SetupPromptRow,
  flavor: AiPromptFlavor = "generic",
): string {
  // Blocks joined by a blank line, so the prompt reads cleanly when pasted.
  return [REPO_ROLE[flavor], `${opening(row)} ${ask(row)}`, itemLines(row.items)]
    .filter(Boolean)
    .join("\n\n");
}
