/**
 * The prompt for one area: its worst files and its highest-priority findings,
 * handed to an agent as one bounded piece of work.
 */

import { splitByOrigin } from "../biomarker-glossary";
import { formatScore } from "@repowise-dev/types/health";
import { findingEntries, historyContextBlock, type PromptFinding } from "./findings";
import {
  bulletList,
  closingSections,
  joinSections,
  pluralS,
  preamble,
  repoSuffix,
  type AiPromptFlavor,
} from "./shared";

export interface AreaPromptFinding extends Omit<PromptFinding, "health_impact" | "reason"> {
  file_path: string;
  health_impact: number | null;
  reason: string | null;
}

export interface AreaPromptFile {
  path: string;
  score: number;
  nloc: number;
}

export interface BuildAreaPromptOptions {
  /** The area's path; `""` is the repository. */
  area: string;
  score: number | null;
  fileCount: number;
  findings: AreaPromptFinding[];
  worstFiles: AreaPromptFile[];
  flavor?: AiPromptFlavor;
  repoName?: string;
}

const PARTS = {
  source:
    "Repowise's static and git analysis flagged the findings below as the highest-priority work in one area of the codebase.",
  open: "each file the findings name, its callers and its tests",
  mcpTail:
    "Pull what repowise already computed before reading by hand: `get_health([file])` for each file's scored findings, `get_context([file])` for its skeleton and `get_risk([file])` for blast radius and test gaps before editing.",
};

const CONSTRAINTS = [
  "Work file by file, highest impact first. One focused, independently revertible change per file.",
  "Confirm each finding in the code before changing anything. Skip a false positive and say why.",
  "Preserve behaviour. Refactors only; add or update tests for any logic you touch.",
  "Stay inside the area unless a fix needs a direct caller or test outside it.",
];

const EXPECTED = [
  "1. A short plan listing the files you will change, in order, and why.",
  "2. The edits, one file at a time.",
  "3. For each finding: fixed, skipped as a false positive, or deferred with a reason.",
];

function asPromptFinding(f: AreaPromptFinding): PromptFinding {
  return { ...f, health_impact: f.health_impact ?? 0, reason: f.reason ?? "" };
}

/** Findings grouped by file, files in first-seen (priority) order. */
function byFile(findings: AreaPromptFinding[]): Map<string, PromptFinding[]> {
  const out = new Map<string, PromptFinding[]>();
  for (const f of findings) {
    const list = out.get(f.file_path);
    if (list) list.push(asPromptFinding(f));
    else out.set(f.file_path, [asPromptFinding(f)]);
  }
  return out;
}

export function buildAreaAiPrompt({
  area,
  score,
  fileCount,
  findings,
  worstFiles,
  flavor = "generic",
  repoName,
}: BuildAreaPromptOptions): string {
  const name = area ? `\`${area}/\`` : "the repository root";
  const sections: string[] = [];
  for (const [path, list] of byFile(findings)) {
    const { codeShape, history } = splitByOrigin(list);
    const historyBlock = historyContextBlock(history);
    sections.push(
      [
        `### \`${path}\``,
        "",
        codeShape.length ? findingEntries(codeShape) : "No structural finding; history only.",
        historyBlock ? `\n${historyBlock}` : "",
        "",
      ].join("\n"),
    );
  }
  return joinSections([
    preamble(flavor, PARTS),
    "",
    `## Area${repoSuffix(repoName)}`,
    "",
    bulletList([
      `Path: ${name}`,
      score != null ? `Health: **${formatScore(score)}/10** across ${fileCount} file${pluralS(fileCount)}` : null,
      worstFiles.length
        ? `Lowest-scoring files: ${worstFiles
            .slice(0, 5)
            .map((f) => `\`${f.path}\` (${formatScore(f.score)})`)
            .join(", ")}`
        : null,
    ]),
    "",
    `## Findings to work through (${findings.length})`,
    "",
    ...sections,
    ...closingSections(CONSTRAINTS, EXPECTED),
  ]);
}
