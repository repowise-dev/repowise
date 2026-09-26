import { bulletList, FLAVOR_PREAMBLE, type AiPromptFlavor } from "./shared";

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

/**
 * Ask an agent to repair documentation whose assertions no longer hold.
 *
 * The one thing this prompt must get right is the direction of the claim. A
 * drift finding is filed against the *document*, and the target is what that
 * document claims exists — an agent that reads it the other way round goes off
 * and "restores" a file the repository deliberately removed. Every section
 * here names the document as the thing to edit.
 *
 * It also has to leave room for the finding to be wrong. Some of them are
 * correct-by-design: a contributor guide teaching you to add a file names one
 * that was never meant to exist, and deleting that line would damage the guide.
 * The flavor preamble already says "leads, not ground truth"; the constraints
 * say what that means for prose specifically.
 */
export function buildDocDriftAiPrompt({
  findings,
  flavor = "generic",
  repoName,
  basis,
}: BuildDocDriftPromptOptions): string {
  const repoLine = repoName ? ` (\`${repoName}\`)` : "";
  const shown = findings.slice(0, MAX_DOC_DRIFT_FINDINGS);
  const hidden = findings.length - shown.length;
  const documents = new Set(findings.map((f) => f.file_path)).size;

  const findingBlock = shown
    .map((f) => {
      // The heading trail the resolver recorded, which is how a reader finds
      // the passage in a long document. Split out of the evidence lines so it
      // can be labelled rather than dumped as trace.
      const trail = (f.evidence ?? []).find((line) => line.startsWith("under: "));
      const trace = (f.evidence ?? []).filter((line) => !line.startsWith("under: "));
      return [
        `- \`${f.file_path}:${f.line_number}\` claims \`${f.target}\` exists`,
        f.reason ? `  - Why flagged: ${f.reason}` : null,
        f.context ? `  - The line as written: \`${f.context.trim()}\`` : null,
        f.raw && f.raw !== f.context ? `  - The reference itself: \`${f.raw}\`` : null,
        trail ? `  - Section: ${trail.slice("under: ".length)}` : null,
        trace.length ? `  - Checked: ${trace.join("; ")}` : null,
        f.confidence != null ? `  - Confidence: ${f.confidence.toFixed(2)}` : null,
      ]
        .filter(Boolean)
        .join("\n");
    })
    .join("\n");

  const hiddenLine =
    hidden > 0
      ? `…and ${hidden} more finding${hidden === 1 ? "" : "s"} in the same slice (open the Doc drift tab in repowise for the full list).`
      : null;

  const constraintList = [
    "**Edit the document, not the code.** Each entry names a document that makes a claim the repository no longer satisfies. The fix is almost always to correct the prose, the path or the link — not to recreate the file it names.",
    "**Find out what replaced the target before you touch the line.** A path that no longer resolves usually moved or was renamed; point the document at the new location rather than deleting the sentence around it.",
    "**Some findings are correct as written.** A guide that teaches the reader to add a file names one that was never meant to exist, and an example path inside a tutorial is not drift. If a line is doing its job, leave it and say so.",
    "**Do not touch a changelog or release note.** Those describe the repository as it was at a release; a reference that no longer resolves is the document doing its job.",
    "**Keep the surrounding prose true.** Fixing a link that sits inside a sentence about how something works means checking that the sentence is still accurate, not just that the path resolves.",
  ];

  const completionContract = [
    "1. The edits themselves, one document at a time.",
    "2. For each, what the target became and how you confirmed it (the file you found, the heading you matched).",
    "3. A list of any findings you left alone, with the reason the line is correct as written.",
  ];

  return [
    FLAVOR_PREAMBLE[flavor],
    "",
    `## Documentation drift${repoLine}`,
    "",
    bulletList([
      `Findings: **${findings.length}** across **${documents}** document${documents === 1 ? "" : "s"}`,
      "Each one is an assertion a document makes that the repository no longer satisfies.",
      basis ?? null,
    ]),
    "",
    "## Findings",
    "",
    findingBlock,
    hiddenLine ?? "",
    "",
    "## Hard constraints",
    "",
    bulletList(constraintList),
    "",
    "## What I expect back",
    "",
    completionContract.join("\n"),
    "",
    flavor === "claude-code-mcp"
      ? "For each target, call `get_context([\"<target>\"])` to find where it lives now — repowise already indexed the tree, so use it instead of globbing for a renamed path. `get_why(...)` on a target that is genuinely gone will often name the change that removed it, which is what the document should now say."
      : "For each target, search the repository for the file or heading it names before editing — most of these moved rather than disappeared, and the document should point at where they went.",
  ]
    .filter((s) => s !== "")
    .join("\n");
}
