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

/**
 * The "Connect evidence" ask, word for word as core's
 * `agent_prompts/evidence.py` `CONNECT_EVIDENCE` (a parity test holds the
 * two equal). The commands come from `repowise evidence setup`, so none is
 * named here.
 */
export const CONNECT_EVIDENCE =
  "Connect runtime evidence to Repowise for this repository. From the repository root, run `repowise evidence setup --format json`. It prints, for each build unit, how to collect coverage and, where it can, profiles, and a `next_call`: the steps to run next, each with the directory (`cwd`) it runs in. Run those steps in order, exactly as printed, without changing their flags. If a step fails, read the error, fix the cause (a missing tool or dependency) and run the step again. When the steps are done, run `repowise evidence setup --format json` again and follow the new `next_call`. Stop when `next_call` is null or repeats steps you already ran. Then tell me what `repowise evidence status` reports, and the `reason` setup gave or why you stopped.";

/** A prompt that has the agent collect coverage and profiles through `repowise evidence setup`. */
export function buildConnectEvidencePrompt(flavor: AiPromptFlavor = "generic"): string {
  return [REPO_ROLE[flavor], CONNECT_EVIDENCE].join("\n\n");
}
