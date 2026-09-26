import { bulletList, FLAVOR_PREAMBLE, type AiPromptFlavor } from "./shared";

// ─────────────────────────────────────────────────────────────────────
// Commit review prompt (per commit)
// ─────────────────────────────────────────────────────────────────────

export interface CommitPromptInput {
  sha: string;
  subject: string;
  review_priority?: string | null;
  risk_percentile?: number | null;
  change_risk_score?: number | null;
  is_fix?: boolean;
  files_changed?: number | null;
  lines_added?: number | null;
  lines_deleted?: number | null;
  entropy?: number | null;
  top_drivers?: string[];
  author_name?: string | null;
}

export interface BuildCommitPromptOptions {
  commit: CommitPromptInput;
  flavor?: AiPromptFlavor;
  repoName?: string;
}

export function buildCommitAiPrompt({
  commit: c,
  flavor = "generic",
  repoName,
}: BuildCommitPromptOptions): string {
  const repoLine = repoName ? ` (\`${repoName}\`)` : "";
  const short = c.sha.slice(0, 10);

  const constraintList = [
    "Read the diff first. The calibrated diff-size score is supporting evidence, not a verdict — a high score on a mechanical rename is fine; a low score hiding a logic change is not.",
    "Focus on what the change-risk drivers flag: scattered edits, missing tests, a hotspot touch, a new-to-the-area author. Confirm each against the actual diff.",
    "Check the blast radius: what depends on the changed files, and is anything that usually changes with them missing from this commit?",
    "Call out missing or weak test coverage for the behavior this commit changes.",
    "Be specific — reference files and lines. A review that says 'looks risky' is useless.",
  ];

  const completionContract = [
    "1. A one-paragraph risk read: is this commit actually risky, and where?",
    "2. The specific things a reviewer should scrutinize, as a checklist tied to files.",
    "3. Suggested reviewers — who owns or recently changed the affected code.",
    "4. Any missing tests or co-change partners that should have been in this commit.",
  ];

  const closer =
    flavor === "claude-code-mcp"
      ? `Call \`get_risk(changed_files=[...])\` for this commit's files to get the blast radius, co-change partners, missing-test directive, and owners in one shot — repowise computes all of that. Then read the diff and ground each flag.`
      : `Start with \`git show ${short}\` to read the diff, then check who owns and recently touched the changed files. Ground every risk call in the actual change.`;

  return [
    FLAVOR_PREAMBLE[flavor],
    "",
    `## Commit to review${repoLine}`,
    "",
    bulletList([
      `Commit: \`${short}\` — ${c.subject || "(no subject)"}`,
      c.author_name ? `Author: ${c.author_name}` : null,
      c.is_fix ? "Tagged as a bug-fix commit." : null,
      c.review_priority ? `Review priority (repo-relative): **${c.review_priority}**` : null,
      c.risk_percentile != null ? `Diff-shape percentile in this repo: ${Math.round(c.risk_percentile)}th` : null,
      c.change_risk_score != null ? `Supporting diff-size score: ${c.change_risk_score.toFixed(1)}/10 (calibrated per commit)` : null,
      c.files_changed != null ? `Files changed: ${c.files_changed}` : null,
      c.lines_added != null || c.lines_deleted != null
        ? `Lines: +${c.lines_added ?? 0} / −${c.lines_deleted ?? 0}`
        : null,
      c.entropy != null ? `Change entropy: ${c.entropy.toFixed(2)} (how scattered the edits are)` : null,
      c.top_drivers && c.top_drivers.length > 0
        ? `Top risk drivers: ${c.top_drivers.slice(0, 3).join(", ")}`
        : null,
    ]),
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
