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
// Work-queue prompt (repo-level: the Attention Needed backlog)
// ─────────────────────────────────────────────────────────────────────

const WORK_QUEUE_PARTS = {
  source:
    "Repowise flagged the backlog below from static and git analysis. Work through it in priority order, one focused, independently-revertible change at a time.",
  open: "the target each item names",
  mcpTail:
    "For each item, pull the context repowise already computed instead of re-exploring by hand: `get_context([target])` for the skeleton, `get_risk([target])` before editing, `get_why(...)` for decision items, `get_health([target])` for code-health items.",
};

const WORK_QUEUE_GUIDANCE: Record<string, string> = {
  stale_decision:
    "Re-check this architectural decision against the current code; update it, or supersede it if the code has moved on.",
  proposed_decision:
    "Review this auto-proposed decision: confirm it reflects reality and accept it, or reject it with a reason.",
  knowledge_silo:
    "One person holds the knowledge for this area (low bus factor). Add tests and docs that make it legible to others.",
  ungoverned_hotspot:
    "A high-churn file with no governing decision. Stabilize it (tests, clearer seams) and/or capture the decision behind it.",
  dead_code:
    "Verify with a repo-wide search (including dynamic references), then remove it in a small, revertible commit.",
};

export interface WorkQueueItem {
  type: string;
  title: string;
  description: string;
  severity: "critical" | "high" | "medium" | "low";
  target_id?: string | null;
}

export interface BuildWorkQueuePromptOptions {
  items: WorkQueueItem[];
  flavor?: AiPromptFlavor;
  repoName?: string;
}

const MAX_WORK_QUEUE_ITEMS = 15;
const SEVERITY_ORDER: Record<string, number> = { high: 0, medium: 1, low: 2 };

const CONSTRAINTS = [
  "Work top-down by severity. Finish (or consciously defer) one item before starting the next.",
  "One focused, independently-revertible change per item — don't bundle unrelated fixes into a single commit.",
  "Verify each item against the real code first. If it's a false positive, skip it and record why instead of forcing a change.",
  "Preserve behavior. Add or update tests for anything whose logic you touch.",
  "If an item is too large for one pass, propose a phased plan for it and move on rather than half-finishing.",
];

const EXPECTED = [
  "1. A triaged plan: the order you'll take these in and why.",
  "2. For each item you action: the change, scoped and verified, with the tests that cover it.",
  "3. For each item you skip: a one-line reason (false positive, needs product input, too large — with a proposed follow-up).",
  "4. A short summary of what's left in the queue at the end.",
];

function workQueueEntry(it: WorkQueueItem, index: number): string {
  const guidance = WORK_QUEUE_GUIDANCE[it.type];
  return [
    `${index + 1}. [${it.severity.toUpperCase()}] **${it.title}**`,
    it.description ? `   - Detail: ${it.description}` : null,
    it.target_id ? `   - Target: \`${it.target_id}\`` : null,
    guidance ? `   - How to approach: ${guidance}` : null,
  ]
    .filter(Boolean)
    .join("\n");
}

export function buildWorkQueueAiPrompt({
  items,
  flavor = "generic",
  repoName,
}: BuildWorkQueuePromptOptions): string {
  const ranked = items
    .slice()
    .sort((a, b) => (SEVERITY_ORDER[a.severity] ?? 3) - (SEVERITY_ORDER[b.severity] ?? 3));
  const shown = ranked.slice(0, MAX_WORK_QUEUE_ITEMS);
  const hidden = ranked.length - shown.length;

  return joinSections([
    preamble(flavor, WORK_QUEUE_PARTS),
    "",
    `## Repository backlog${repoSuffix(repoName)}`,
    "",
    bulletList([
      `Items in this queue: **${ranked.length}**`,
      "Source: repowise's Attention Needed panel (decisions, hotspots, knowledge silos, dead code).",
    ]),
    "",
    "## Issues to work through (highest severity first)",
    "",
    shown.map(workQueueEntry).join("\n\n"),
    hidden > 0
      ? `\n…and ${hidden} more lower-priority item${pluralS(hidden)} in the panel — handle these after the above.`
      : "",
    "",
    verifySection(null, []),
    "",
    ...closingSections(CONSTRAINTS, EXPECTED),
    flavor === "claude-code-mcp"
      ? "Start by calling `get_overview()` to orient, then take the queue top-down — `get_context` / `get_risk` / `get_why` per item before you touch anything. repowise already did the exploration; lean on it."
      : "Start with the highest-severity items and ground each one in the real code before acting. The list describes symptoms; confirm the root cause before you change anything.",
  ]);
}
