import {
  bulletList,
  closingSections,
  explorationCloser,
  FLAVOR_PREAMBLE,
  joinSections,
  repoSuffix,
  type AiPromptFlavor,
} from "./shared";

// ─────────────────────────────────────────────────────────────────────
// Hotspot stabilization prompt (per file)
// ─────────────────────────────────────────────────────────────────────

export interface HotspotPromptInput {
  file_path: string;
  churn_percentile?: number | null;
  commit_count_90d?: number | null;
  commit_count_30d?: number | null;
  bus_factor?: number | null;
  contributor_count?: number | null;
  primary_owner?: string | null;
  lines_added_90d?: number | null;
  lines_deleted_90d?: number | null;
  temporal_hotspot_score?: number | null;
  change_entropy_pct?: number | null;
  prior_defect_count?: number | null;
  module?: string | null;
}

export interface BuildHotspotPromptOptions {
  hotspot: HotspotPromptInput;
  flavor?: AiPromptFlavor;
  repoName?: string;
}

const EXPECTED = [
  "1. A diagnosis: why does this file churn so much? (2–4 bullets, grounded in the actual code and its history.)",
  "2. A prioritized plan to reduce its change-cost — structural seams, extractions, or decoupling, smallest-risk first.",
  "3. The change you'd make first, scoped and behavior-preserving, with the tests that protect it.",
  "4. What you'd leave for later and why.",
];

/** The churn, ownership and defect facts that got this file flagged. */
function whyFlagged(h: HotspotPromptInput, soleOwner: boolean): string {
  return bulletList([
    h.churn_percentile != null
      ? `Churn: **${Math.round(h.churn_percentile)}th percentile** in this repo (it changes more than most files)`
      : null,
    h.commit_count_90d != null
      ? `Commits: **${h.commit_count_90d} in 90 days**${h.commit_count_30d != null ? ` (${h.commit_count_30d} in the last 30)` : ""}`
      : null,
    h.bus_factor != null
      ? `Bus factor: **${h.bus_factor}**${soleOwner ? " — knowledge concentrated in one person" : ""}`
      : null,
    h.contributor_count != null ? `Contributors: ${h.contributor_count}` : null,
    h.primary_owner ? `Primary owner: ${h.primary_owner}` : null,
    h.lines_added_90d != null || h.lines_deleted_90d != null
      ? `Lines churned (90d): +${h.lines_added_90d ?? 0} / −${h.lines_deleted_90d ?? 0}`
      : null,
    h.change_entropy_pct != null
      ? `Change entropy: ${Math.round(h.change_entropy_pct)}th percentile (how scattered the edits are)`
      : null,
    h.prior_defect_count != null && h.prior_defect_count > 0
      ? `Prior bug-fix commits here: ${h.prior_defect_count}`
      : null,
    h.module ? `Module: \`${h.module}\`` : null,
  ]);
}

export function buildHotspotAiPrompt({
  hotspot: h,
  flavor = "generic",
  repoName,
}: BuildHotspotPromptOptions): string {
  const soleOwner = h.bus_factor != null && h.bus_factor <= 1;

  const constraintList = [
    "**Understand the churn before touching it.** A hotspot is a file that keeps changing — find out why (a god module, mixed responsibilities, a leaky abstraction, missing tests) before proposing structure changes.",
    "Make it safer to change, don't just rewrite it. Behavior-preserving refactors, better seams, and tests beat a from-scratch rewrite.",
    soleOwner
      ? "This file has a low bus factor (one person holds the knowledge). Favor changes that make it more legible to others — clear names, docs on the non-obvious parts, tests that document intent."
      : "Keep the change reviewable — a single coherent improvement, not a sprawling rewrite.",
    "Because this file changes often, raise its test coverage as part of the work — that's what makes future changes cheap.",
    "Check its co-change partners: if it always changes alongside another file, the coupling itself may be the thing to fix.",
  ];

  return joinSections([
    FLAVOR_PREAMBLE[flavor],
    "",
    `## Hotspot to stabilize${repoSuffix(repoName)}`,
    "",
    `\`${h.file_path}\``,
    "",
    "## Why it's flagged",
    "",
    whyFlagged(h, soleOwner),
    "",
    ...closingSections(constraintList, EXPECTED),
    explorationCloser(flavor, h.file_path, "hotspot"),
  ]);
}
