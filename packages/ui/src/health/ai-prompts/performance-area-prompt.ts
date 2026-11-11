import type { PerformanceOpportunity } from "@repowise-dev/types/health";

import { performanceCauseLine } from "./performance-causes";
import {
  bulletList,
  closingSections,
  joinSections,
  pluralS,
  preamble,
  repoSuffix,
  verifySection,
  type AiPromptFlavor,
} from "./shared";

// ─────────────────────────────────────────────────────────────────────
// Performance causes in one area (the queue's bulk handoff)
// ─────────────────────────────────────────────────────────────────────

const PARTS = {
  source:
    "Repowise's static performance analysis grouped the repeated work below into causes, each one place to edit. They are reads of the code's shape, not measurements of a running system.",
  open: "the file and function each cause names, its callers and its tests",
  mcpTail:
    "For each cause, call `get_health(opportunity_id=\"...\")` with its id for the plan, its steps and the tests that guard it, then `get_risk([file])` before editing.",
};

/** Above this the prompt stops listing and says how many more there are. */
const MAX_CAUSES = 12;

const CONSTRAINTS = [
  "Take the causes in the order given; it is the analysis's ranking.",
  "One cause per change, each independently revertible.",
  "Confirm each cause against the code before editing. If the loop is bounded or the repetition is intended, skip it and say why.",
  "Preserve results exactly: a batched or reordered call must return what the loop returned.",
  "Run the tests that exercise each function before and after its change.",
];

const EXPECTED = [
  "1. The order you will take the causes in, and any you will skip with the reason.",
  "2. For each cause you change: the edit, the tests you ran, and why the result is unchanged.",
  "3. What is left, if anything, with a proposed next step.",
];

export interface BuildPerformanceAreaPromptOptions {
  /** The directory the causes share; empty for the repository root. */
  area: string;
  opportunities: PerformanceOpportunity[];
  /** The queue runs past the loaded page, so the area may hold more causes than these. */
  paged?: boolean;
  flavor?: AiPromptFlavor;
  repoName?: string;
}

function causeEntry(c: PerformanceOpportunity, index: number): string {
  return `${index + 1}. ${performanceCauseLine(c)}\n   - Opportunity id: \`${c.opportunity_id}\`\n   - Edit in: \`${c.file_path}\``;
}

export function buildPerformanceAreaPrompt({
  area,
  opportunities,
  paged = false,
  flavor = "generic",
  repoName,
}: BuildPerformanceAreaPromptOptions): string {
  const shown = opportunities.slice(0, MAX_CAUSES);
  const hidden = opportunities.length - shown.length;
  const files = Array.from(new Set(opportunities.map((c) => c.file_path)));
  return joinSections([
    preamble(flavor, PARTS),
    "",
    `## Performance causes in \`${area || "(repository root)"}\`${repoSuffix(repoName)}`,
    "",
    bulletList([
      `Causes: **${opportunities.length}** across ${files.length} file${pluralS(files.length)}.`,
      "Each cause folds every call site that shares one fix, so one edit settles all of them.",
      paged
        ? "These are the area's causes on one page of the ranked queue; later pages may hold more in this area."
        : null,
    ]),
    "",
    shown.map(causeEntry).join("\n\n"),
    hidden > 0 ? `\n…and ${hidden} more in this area; take them after the above.` : "",
    "",
    verifySection(null, files.slice(0, 5)),
    "",
    ...closingSections(CONSTRAINTS, EXPECTED),
  ]);
}
