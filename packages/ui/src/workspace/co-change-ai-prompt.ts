/**
 * Agent prompts for cross-repo co-changes: one file pair, and one repository
 * pair with its strongest file pairs as evidence.
 *
 * Same shape as the health and contract builders (role, evidence, tasks,
 * constraints, what to hand back). Co-change is a work pattern read from git
 * history, not a declared dependency, so every prompt asks the agent to find
 * the shared concept first and only then decide whether a contract, a shared
 * type or a generated client should replace the manual lockstep.
 */

import type { AiPromptFlavor } from "../health/ai-prompt-builder";
import type { WorkspaceCoChangeEntry } from "@repowise-dev/types/workspace";
import {
  gapPhrase,
  hiddenCouplingSentence,
  MAX_COMMIT_PAIRS,
  SESSION_WINDOW_HOURS,
  capRule,
  type CoChangeCaps,
  type CoChangeStructureFacts,
  type NearbyCommitPair,
} from "./co-change-facts";

/** File pairs listed in a repository-pair prompt before the rest become a count. */
export const MAX_PROMPT_PAIRS = 15;

const PREAMBLE: Record<AiPromptFlavor, string> = {
  generic:
    "You are a senior engineer working across the repositories of one workspace. The evidence below is a work pattern repowise mined from git history: files in different repositories that the same author changed in the same work sessions. It is a lead, not a verified dependency. Open the files it names and read the commits before you conclude anything.",
  "claude-code":
    "You are Claude Code working across the repositories of one workspace. The evidence below is a work pattern repowise mined from git history: files in different repositories that the same author changed in the same work sessions. Treat it as a lead, not a verified dependency. Use Read, Grep and Glob on the files it names, and `git log` in each repository, before planning edits. Use TodoWrite for non-trivial steps.",
  "claude-code-mcp":
    "You are Claude Code working across the repositories of one workspace indexed by repowise, with its MCP tools available. The evidence below is a work pattern repowise mined from git history: files in different repositories that the same author changed in the same work sessions. Treat it as a lead, not a verified dependency. Pull what repowise already computed before re-reading by hand: `get_context([...])` for a file's skeleton, `get_risk([...])` for its co-change partners and test gaps, `get_why(...)` for the decision behind its shape, `search_codebase` to find a type or route by name, and `get_blast_radius` for which services a change reaches. Fall back to Read / Grep only for what the index cannot serve.",
  cursor:
    "Work across the repositories referenced below. The evidence is a work pattern repowise mined from git history: files in different repositories that the same author changed in the same work sessions. Treat it as a lead, not a verified dependency. Use @file and @codebase to read every file it names before editing.",
};

function bullets(items: (string | null | undefined | false)[]): string {
  return items
    .filter(Boolean)
    .map((s) => `- ${s}`)
    .join("\n");
}

function join(sections: (string | null | undefined | false)[]): string {
  return sections.filter((s) => s !== null && s !== undefined && s !== false && s !== "").join("\n");
}

function where(repo: string, file: string): string {
  return `\`${repo}\` \`${file}\``;
}

function pct(v: number): string {
  return `${Math.round(v * 100)}%`;
}

function sessions(n: number): string {
  return `${n} shared work ${n === 1 ? "session" : "sessions"}`;
}

// ---------------------------------------------------------------------------
// One file pair
// ---------------------------------------------------------------------------

/** What the per-repo git index says about one side, when it was fetched. */
export interface CoChangeFileFacts {
  commits90d?: number | null;
  churnPercentile?: number | null;
  priorFixes?: number | null;
  isHotspot?: boolean | null;
}

export interface BuildCoChangePairPromptOptions {
  pair: WorkspaceCoChangeEntry;
  flavor?: AiPromptFlavor;
  structure?: CoChangeStructureFacts | null;
  sourceFacts?: CoChangeFileFacts | null;
  targetFacts?: CoChangeFileFacts | null;
  commits?: NearbyCommitPair[];
}

function factsTail(f: CoChangeFileFacts | null | undefined): string {
  if (!f) return "";
  const parts = [
    f.commits90d != null ? `${f.commits90d} commits in 90 days` : null,
    f.churnPercentile != null ? `churn percentile ${Math.round(f.churnPercentile)}` : null,
    f.priorFixes ? `${f.priorFixes} prior bug fixes` : null,
    f.isHotspot ? "a hotspot" : null,
  ].filter(Boolean);
  return parts.length ? ` (${parts.join(", ")})` : "";
}

function structureLine(pair: WorkspaceCoChangeEntry, s: CoChangeStructureFacts | null | undefined) {
  if (!s) return "Declared link: not checked. Look for a contract or shared package between them yourself.";
  if (s.pairLinks.length > 0) {
    const ids = s.pairLinks
      .slice(0, 5)
      .map((l) => `\`${l.contract_id}\` (${l.contract_type}, ${l.provider_repo} provides, ${l.consumer_repo} consumes)`)
      .join("; ");
    return `Declared link: these files are joined by ${s.pairLinks.length === 1 ? "a contract" : `${s.pairLinks.length} contracts`}: ${ids}. The co-change is at least partly explained; judge whether the contract is the right shape.`;
  }
  return `Declared link: **none**. ${hiddenCouplingSentence(pair, s, (r) => `\`${r}\``)}`;
}

function commitLine(c: NearbyCommitPair, pair: WorkspaceCoChangeEntry): string {
  const day = c.source.date.slice(0, 10);
  const msg = (m?: string | null) => (m ? ` "${m.split("\n")[0]}"` : "");
  return `${day}: \`${pair.source_repo}\` ${c.source.sha.slice(0, 8)}${msg(c.source.message)} and \`${pair.target_repo}\` ${c.target.sha.slice(0, 8)}${msg(c.target.message)} (${c.source.author ?? "same author"}, ${gapPhrase(c.gapHours)})`;
}

export function buildCoChangePairAiPrompt({
  pair,
  flavor = "generic",
  structure,
  sourceFacts,
  targetFacts,
  commits = [],
}: BuildCoChangePairPromptOptions): string {
  const a = where(pair.source_repo, pair.source_file);
  const b = where(pair.target_repo, pair.target_file);
  const shownCommits = commits.slice(0, MAX_COMMIT_PAIRS);

  const closer =
    flavor === "claude-code-mcp"
      ? `Call \`get_context(['${pair.source_file}'])\` in \`${pair.source_repo}\` and \`get_context(['${pair.target_file}'])\` in \`${pair.target_repo}\`, \`get_risk([...])\` on each for its other co-change partners and test gaps, \`get_why(...)\` for any decision that governs either file, and \`get_blast_radius\` for what else in the workspace a change to either reaches. Don't restructure until you can name why they move together.`
      : `Read both files, then run \`git log\` for each in its own repository around the shared dates. The session count is a symptom; find the shared concept driving it before you change anything.`;

  return join([
    PREAMBLE[flavor],
    "",
    "## Two files that change together across repositories",
    "",
    bullets([
      `File A: ${a}${factsTail(sourceFacts)}`,
      `File B: ${b}${factsTail(targetFacts)}`,
      `Shared work sessions: **${pair.frequency}** (the same author changed both, with commits in the two repositories no more than ${SESSION_WINDOW_HOURS} hours apart)`,
      `Strength: **${pct(pair.strength)}**, the recency-weighted share of the less active file's work sessions that also touched the other. A work-pattern signal, not a verified dependency.`,
      pair.last_date ? `Last changed together: ${pair.last_date.slice(0, 10)}` : null,
      structureLine(pair, structure),
      "Source: repowise cross-repo co-change mining over each repository's git history. Treat it as a lead.",
    ]),
    "",
    shownCommits.length > 0
      ? join([
          "## Commits close together",
          "",
          bullets(shownCommits.map((c) => commitLine(c, pair))),
          "",
          "Reconstructed from each file's recent significant commits: the miner does not record which sessions it counted, so some may be missing.",
          "",
        ])
      : "",
    "## What to find out",
    "",
    "1. Why do these two files change together? Read both and the commits that touched them in the same sessions, then name the shared concept: a wire type, an API shape, a config key, documentation of code, generated output, or something else.",
    "2. Is the lockstep manual? If one file restates what the other defines (a schema retyped by hand, a route described in prose, a client written against a server), decide whether a declared contract, a shared type package, or a generated client or document should own it so the second edit disappears.",
    "3. If the coupling is legitimate and cheap to keep (documentation that must track code), say so and propose the lightest guard: a test, a CI check, or a note in each file naming its partner.",
    "",
    "## Hard constraints",
    "",
    bullets([
      "**Diagnose before changing.** Name the shared concept and cite the lines on both sides before proposing anything.",
      "**When you edit either file, check the other in the same change.** Until the coupling is removed, a change to one is half a change.",
      "Keep the contract between the repositories stable. Do not move code across a repository boundary without saying why the boundary is wrong.",
      "Preserve behavior. Add or update a test or check that fails when the two drift apart.",
      "If the evidence turns out to be coincidence (unrelated work in one sitting), say so and stop.",
    ]),
    "",
    "## What I expect back",
    "",
    "1. A verdict: the shared concept, and whether the coupling is accidental, legitimate but manual, or legitimate and fine.",
    "2. If manual: what should own the shared concept (contract, shared type, generated client or doc), where it would live, and the smallest safe first step.",
    "3. The first change, scoped, with the test or check that guards it.",
    "4. If the coupling stays: the one-line note to add to each file naming its partner.",
    "",
    closer,
  ]);
}

// ---------------------------------------------------------------------------
// One repository pair
// ---------------------------------------------------------------------------

export interface BuildCoChangeRepoPairPromptOptions {
  repo1: string;
  repo2: string;
  /** Every file pair shown for this repository pair; ranked here by strength. */
  pairs: WorkspaceCoChangeEntry[];
  flavor?: AiPromptFlavor;
  /** Which cap trimmed the mined pairs, when the server says. */
  caps?: CoChangeCaps | null;
}

/** Files that recur across several pairs: usually where the shared concept lives. */
function recurringFiles(pairs: WorkspaceCoChangeEntry[]): { repo: string; file: string; n: number }[] {
  const counts = new Map<string, { repo: string; file: string; n: number }>();
  for (const p of pairs) {
    for (const [repo, file] of [
      [p.source_repo, p.source_file],
      [p.target_repo, p.target_file],
    ] as const) {
      const key = `${repo}\u0000${file}`;
      const hit = counts.get(key) ?? { repo, file, n: 0 };
      hit.n += 1;
      counts.set(key, hit);
    }
  }
  return [...counts.values()]
    .filter((c) => c.n >= 2)
    .sort((a, b) => b.n - a.n || a.file.localeCompare(b.file))
    .slice(0, 8);
}

export function buildCoChangeRepoPairAiPrompt({
  repo1,
  repo2,
  pairs,
  flavor = "generic",
  caps,
}: BuildCoChangeRepoPairPromptOptions): string {
  const ranked = pairs.slice().sort((a, b) => b.strength - a.strength || b.frequency - a.frequency);
  const shown = ranked.slice(0, MAX_PROMPT_PAIRS);
  const hidden = ranked.length - shown.length;
  const latest = ranked.reduce((d, p) => (p.last_date > d ? p.last_date : d), "");
  const hubs = recurringFiles(ranked);
  // A global cap may have trimmed any pair; the per-pair cap only one that filled it.
  const rule = caps ? capRule(caps) : null;
  const capped =
    rule !== null &&
    (caps!.truncatedBy === "total" || (caps!.perRepoPairCap != null && ranked.length >= caps!.perRepoPairCap));

  const closer =
    flavor === "claude-code-mcp"
      ? `Start with \`get_blast_radius\` on \`${repo1}\` and \`${repo2}\` to see what already connects them, then \`get_context\` and \`get_risk\` on the recurring files above. \`search_codebase\` for the type or route names you find on both sides. repowise already mapped the contracts and history; use it before grepping.`
      : "Start with the recurring files: they are usually where the shared concept lives. Read them in both repositories and the commits that changed them together before proposing anything.";

  return join([
    PREAMBLE[flavor],
    "",
    "## Two repositories that change together",
    "",
    bullets([
      `Repositories: \`${repo1}\` and \`${repo2}\``,
      `File pairs: **${ranked.length}**${capped ? ` (${rule}, so there are likely more)` : ""}`,
      ranked[0] ? `Strongest pair: ${pct(ranked[0].strength)}` : null,
      latest ? `Latest shared session: ${latest.slice(0, 10)}` : null,
      "Source: repowise cross-repo co-change mining over git history. Treat it as a lead.",
    ]),
    "",
    "## Strongest file pairs",
    "",
    shown
      .map(
        (p, i) =>
          `${i + 1}. ${where(p.source_repo, p.source_file)} with ${where(p.target_repo, p.target_file)}: ${pct(p.strength)}, ${sessions(p.frequency)}, last ${p.last_date.slice(0, 10)}`,
      )
      .join("\n"),
    hidden > 0 ? `...and ${hidden} more file pairs not listed.` : "",
    "",
    hubs.length > 0
      ? join([
          "## Files that recur across pairs",
          "",
          bullets(hubs.map((h) => `${where(h.repo, h.file)} appears in ${h.n} pairs`)),
          "",
        ])
      : "",
    "## What to find out",
    "",
    "1. Group the pairs by the concept they share (an API surface, a schema, a set of docs describing code, release plumbing).",
    "2. For each group, check whether a declared contract, shared package or generated client already covers it. If not, say whether one should, and what it would replace.",
    "3. Rank the groups by payoff: how often they force a second edit, and how bad a missed edit would be.",
    "",
    "## Hard constraints",
    "",
    bullets([
      "**Diagnose before changing.** Cite the lines on both sides for every group you name.",
      "Keep the contract between the repositories stable, and do not move code across the boundary without saying why the boundary is wrong.",
      "One group per change, each independently revertible, each with a test or check that fails when the two sides drift.",
      "Some groups will be coincidence or legitimate documentation upkeep. Say so and leave them.",
    ]),
    "",
    "## What I expect back",
    "",
    "1. The groups, each with its shared concept and the pairs that belong to it.",
    "2. For each: accidental, legitimate but manual, or legitimate and fine, with the reason.",
    "3. A ranked plan for the manual ones: what should own the concept, where, and the first step.",
    "",
    closer,
  ]);
}
