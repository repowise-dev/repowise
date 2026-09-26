import {
  bulletList,
  closingSections,
  FLAVOR_PREAMBLE,
  joinSections,
  pluralS,
  repoSuffix,
  type AiPromptFlavor,
} from "./shared";

// ─────────────────────────────────────────────────────────────────────
// Documentation drift prompt (per finding, or a whole slice)
// ─────────────────────────────────────────────────────────────────────

export interface DocDriftPromptFinding {
  /** The DOCUMENT carrying the assertion. Never the file it names. */
  file_path: string;
  line_number: number;
  /** What the document claims exists. */
  target: string;
  kind?: string | null;
  confidence?: number | null;
  reason?: string | null;
  /** The source line as written, which is what the agent has to edit. */
  context?: string | null;
  /** The reference exactly as the document wrote it. */
  raw?: string | null;
  /** The resolver's trace, including the enclosing heading trail. */
  evidence?: string[] | null;
}

export interface BuildDocDriftPromptOptions {
  findings: DocDriftPromptFinding[];
  flavor?: AiPromptFlavor;
  repoName?: string;
  /** What the counts do and do not cover, from the engine. */
  basis?: string | null;
}

/** Cap the list so a large pile does not produce a giant prompt. */
const MAX_DOC_DRIFT_FINDINGS = 20;

const CONSTRAINTS = [
  "**Edit the document, not the code.** Each entry names a document that makes a claim the repository no longer satisfies. The fix is almost always to correct the prose, the path or the link — not to recreate the file it names.",
  "**Find out what replaced the target before you touch the line.** A path that no longer resolves usually moved or was renamed; point the document at the new location rather than deleting the sentence around it.",
  "**Some findings are correct as written.** A guide that teaches the reader to add a file names one that was never meant to exist, and an example path inside a tutorial is not drift. If a line is doing its job, leave it and say so.",
  "**Do not touch a changelog or release note.** Those describe the repository as it was at a release; a reference that no longer resolves is the document doing its job.",
  "**Keep the surrounding prose true.** Fixing a link that sits inside a sentence about how something works means checking that the sentence is still accurate, not just that the path resolves.",
];

const EXPECTED = [
  "1. The edits themselves, one document at a time.",
  "2. For each, what the target became and how you confirmed it (the file you found, the heading you matched).",
  "3. A list of any findings you left alone, with the reason the line is correct as written.",
];

const SECTION_PREFIX = "under: ";

function findingEntry(f: DocDriftPromptFinding): string {
  // The heading trail locates the passage, so it gets its own label.
  const trail = (f.evidence ?? []).find((line) => line.startsWith(SECTION_PREFIX));
  const trace = (f.evidence ?? []).filter((line) => !line.startsWith(SECTION_PREFIX));
  return [
    `- \`${f.file_path}:${f.line_number}\` claims \`${f.target}\` exists`,
    f.reason ? `  - Why flagged: ${f.reason}` : null,
    f.context ? `  - The line as written: \`${f.context.trim()}\`` : null,
    f.raw && f.raw !== f.context ? `  - The reference itself: \`${f.raw}\`` : null,
    trail ? `  - Section: ${trail.slice(SECTION_PREFIX.length)}` : null,
    trace.length ? `  - Checked: ${trace.join("; ")}` : null,
    f.confidence != null ? `  - Confidence: ${f.confidence.toFixed(2)}` : null,
  ]
    .filter(Boolean)
    .join("\n");
}

/**
 * Ask an agent to repair documentation whose assertions no longer hold. A
 * finding is filed against the document, so every section names the document
 * as the thing to edit, and the constraints leave room for lines that are
 * correct by design.
 */
export function buildDocDriftAiPrompt({
  findings,
  flavor = "generic",
  repoName,
  basis,
}: BuildDocDriftPromptOptions): string {
  const shown = findings.slice(0, MAX_DOC_DRIFT_FINDINGS);
  const hidden = findings.length - shown.length;
  const documents = new Set(findings.map((f) => f.file_path)).size;

  return joinSections([
    FLAVOR_PREAMBLE[flavor],
    "",
    `## Documentation drift${repoSuffix(repoName)}`,
    "",
    bulletList([
      `Findings: **${findings.length}** across **${documents}** document${pluralS(documents)}`,
      "Each one is an assertion a document makes that the repository no longer satisfies.",
      basis ?? null,
    ]),
    "",
    "## Findings",
    "",
    shown.map(findingEntry).join("\n"),
    hidden > 0
      ? `…and ${hidden} more finding${pluralS(hidden)} in the same slice (open the Doc drift tab in repowise for the full list).`
      : "",
    "",
    ...closingSections(CONSTRAINTS, EXPECTED),
    flavor === "claude-code-mcp"
      ? "For each target, call `get_context([\"<target>\"])` to find where it lives now — repowise already indexed the tree, so use it instead of globbing for a renamed path. `get_why(...)` on a target that is genuinely gone will often name the change that removed it, which is what the document should now say."
      : "For each target, search the repository for the file or heading it names before editing — most of these moved rather than disappeared, and the document should point at where they went.",
  ]);
}
