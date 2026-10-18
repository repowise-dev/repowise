import {
  bulletList,
  closingSections,
  joinSections,
  pluralS,
  preamble,
  repoSuffix,
  type AiPromptFlavor,
} from "./shared";

// ─────────────────────────────────────────────────────────────────────
// Merged change prompt (one PR / MR / change record)
// ─────────────────────────────────────────────────────────────────────

/** One finding the change introduced or made worse. */
export interface ChangePromptFinding {
  change_kind: string;
  severity: string;
  severity_before?: string | null | undefined;
  path: string;
  symbol?: string | null | undefined;
  line_start?: number | null | undefined;
  line_end?: number | null | undefined;
  reason: string;
  /** The commit of the change that charged it, when the rollup knows. */
  sha?: string | null | undefined;
}

export interface ChangePromptInput {
  /** Short name: the forge ref (a PR number) or a trimmed id. */
  label: string;
  title: string;
  head_sha: string;
  commits: string[];
  files: string[];
  /** Findings the change introduced or worsened, worst first. */
  findings: ChangePromptFinding[];
  /** Worse findings past the listed ones (the server sends the worst first, capped). */
  omitted?: number | undefined;
  /** Findings it resolved; counted, they have no location left. */
  resolved: number;
  /** False when part of the change was not compared. */
  complete: boolean;
  /** Evidence rows that need a look, already worded ("Decisions crossed: 4 accepted"). */
  attention: string[];
  /** `commit` when the unit is one commit rather than a merged change. */
  unit?: "change" | "commit" | undefined;
}

/** How the prompt names its unit. */
const UNIT_WORDS = {
  change: { noun: "change", subject: "merged change", heading: "Merged change", fact: "Change", madeFor: "merged for" },
  commit: { noun: "commit", subject: "commit", heading: "Commit", fact: "Commit", madeFor: "made for" },
} as const;

export interface BuildChangePromptOptions {
  change: ChangePromptInput;
  flavor?: AiPromptFlavor;
  repoName?: string;
}

/**
 * Whether the change made anything worse: listed findings, or worse ones past
 * the capped list. The prompt fixes when so, else reviews the flagged rows.
 */
export function changePromptFixes(c: ChangePromptInput): boolean {
  return c.findings.length > 0 || (c.omitted ?? 0) > 0;
}

/** Above this many files the list is cut; the agent can read the rest from git. */
const FILE_CAP = 25;

function at(f: ChangePromptFinding): string {
  if (!f.line_start) return f.path;
  const end = f.line_end && f.line_end !== f.line_start ? `-${f.line_end}` : "";
  return `${f.path}:${f.line_start}${end}`;
}

function findingLine(f: ChangePromptFinding): string {
  const was = f.change_kind === "worsened" && f.severity_before ? `, was ${f.severity_before}` : "";
  const sym = f.symbol ? ` in \`${f.symbol}\`` : "";
  const by = f.sha ? ` (commit \`${f.sha.slice(0, 12)}\`)` : "";
  return `**${f.change_kind}** (${f.severity}${was}) \`${at(f)}\`${sym}${by}: ${f.reason}`;
}

/** How to read the whole change: its commits, or the head when there is one. */
function diffCommand(c: ChangePromptInput): string {
  if (c.commits.length <= 1) return `git show ${c.head_sha.slice(0, 12)}`;
  return c.commits.map((s) => `git show ${s.slice(0, 12)}`).join("; ");
}

function changeFacts(c: ChangePromptInput): string {
  const w = UNIT_WORDS[c.unit ?? "change"];
  const shown = c.files.slice(0, FILE_CAP).map((f) => `\`${f}\``);
  const more = c.files.length > FILE_CAP ? ` and ${c.files.length - FILE_CAP} more` : "";
  return bulletList([
    `${w.fact}: ${c.label} — ${c.title}`,
    `Commits: ${c.commits.length} (head \`${c.head_sha.slice(0, 12)}\`)`,
    `Files (${c.files.length}): ${shown.join(", ")}${more}`,
    c.resolved > 0 ? `It also resolved ${c.resolved} existing finding${pluralS(c.resolved)}; keep those fixes.` : null,
    c.complete ? null : `Part of this ${w.noun} could not be compared, so the list below may be incomplete.`,
    c.omitted
      ? `${c.omitted} more finding${pluralS(c.omitted)} this ${w.noun} made worse ${c.omitted === 1 ? "is" : "are"} not listed below (the list keeps the worst first); after these, check the files above for the rest.`
      : null,
  ]);
}

/**
 * The prompt for one merged change. With findings it asks the agent to fix
 * what the change made worse; without, it asks for a focused review of what
 * the evidence checklist flagged.
 */
export function buildChangeAiPrompt({
  change: c,
  flavor = "generic",
  repoName,
}: BuildChangePromptOptions): string {
  const fixing = changePromptFixes(c);
  const w = UNIT_WORDS[c.unit ?? "change"];
  const lead = preamble(flavor, {
    source: fixing
      ? `Repowise compared code health before and after this ${w.subject} and charged the findings below to it.`
      : `Repowise built an evidence checklist for this ${w.subject}; the rows below are what it flagged.`,
    open: "the files and lines named below",
    mcpTail:
      "Call `get_context([...])` on the files named below and `get_risk(changed_files=[...])` for blast radius and test gaps before reading by hand.",
  });

  const work =
    c.findings.length > 0
      ? [`## What this ${w.noun} made worse`, "", bulletList(c.findings.map(findingLine)), ""]
      : [];
  const flagged = c.attention.length > 0 ? ["## Flagged for a look", "", bulletList(c.attention), ""] : [];

  const constraints = fixing
    ? [
        `Fix the findings above in place. Do not revert the ${w.noun} or undo the behaviour it was ${w.madeFor}.`,
        "Keep each fix small and local to the named symbol; no unrelated refactors.",
        "If a finding is a false positive or the cost is justified, say so and leave it.",
        "Run the tests that cover the touched files before and after.",
      ]
    : [
        "Review only; do not edit unless a flagged row is a real defect.",
        "Ground every point in the diff and name files and lines.",
      ];
  const expected = fixing
    ? [
        "1. For each finding: fixed (with the diff), or why it should stay.",
        "2. The tests you ran and their result.",
      ]
    : [
        `1. For each flagged row: whether it matters for this ${w.noun} and why.`,
        "2. Anything a reviewer should still check, tied to files.",
      ];

  return joinSections([
    lead,
    "",
    `## ${w.heading}${repoSuffix(repoName)}`,
    "",
    changeFacts(c),
    "",
    ...work,
    ...flagged,
    ...closingSections(constraints, expected),
    `Read the ${w.noun} with \`${diffCommand(c)}\` first.`,
  ]);
}
