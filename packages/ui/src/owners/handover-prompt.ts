/**
 * The agent prompt for knowledge handover. Two purposes share one shape:
 *
 * - `handover`: people are leaving (or have gone quiet); plan how the code
 *   they hold passes to whoever stays.
 * - `spread`: one person holds busy or weak code alone; plan how a second
 *   person comes to know it.
 *
 * Both list the areas, the files that matter most in each, and the suggested
 * person, then ask for a plan the team can run, not a code change.
 */

import {
  bulletList,
  closingSections,
  joinSections,
  pluralS,
  preamble,
  repoSuffix,
  type AiPromptFlavor,
} from "../health/ai-prompts/shared";
import { joinNames, mostNamed } from "./knowledge-files";
import type { KnowledgeRiskArea } from "./knowledge-risks";
import type { OffboardingArea } from "./offboarding";

export type HandoverPurpose = "handover" | "spread";

export interface HandoverPromptFile {
  path: string;
  isHotspot: boolean;
  lowHealth: boolean;
  /** Share of commits the holder or leavers made (0-1). */
  share: number;
}

export interface HandoverPromptArea {
  dir: string;
  files: HandoverPromptFile[];
  /** Who could take over (`handover`) or learn it (`spread`). */
  suggested?: string | null;
}

export interface BuildHandoverPromptOptions {
  purpose: HandoverPurpose;
  /** Leavers for `handover`, the holder(s) for `spread`. */
  people: string[];
  areas: HandoverPromptArea[];
  flavor?: AiPromptFlavor;
  repoName?: string;
  /** `handover`: the share the people who stay fall below (0-1). Default 0.5. */
  threshold?: number;
  /** `spread`: why these files matter now. Default: hotspots or weak health. */
  spreadReason?: string;
}

/** Named people past which the prompt says "and N others". */
const MAX_NAMED = 4;

function namePeople(people: readonly string[]): string {
  const named =
    people.length > MAX_NAMED
      ? [...people.slice(0, MAX_NAMED), `${people.length - MAX_NAMED} others`]
      : people;
  return joinNames(named) || "the selected people";
}

/** Past these the prompt says how many more there are instead of listing them. */
const MAX_AREAS = 8;
const MAX_FILES_PER_AREA = 6;

const PARTS = {
  source:
    "Repowise read the git history below: who wrote the commits behind each file, which files churn most (hotspots), and which sit in a weak health band. Shares are of commits, so they describe history, not who understands the code today.",
  open: "each file listed, its tests and its callers",
  mcpTail:
    "Call `get_context([...])` on each area for its documentation and owners, `get_risk([...])` for co-change partners and test gaps, and `get_why(...)` for the decisions behind the code.",
};

const CONSTRAINTS = [
  "Do not change code. This is a plan for people; propose code changes only where they lower the cost of the handover (a missing test, an undocumented invariant), and list them separately.",
  "Ground every claim in the files: name the function, the invariant or the test you are pointing at.",
  "Keep it proportionate: a file that is small and well tested needs a line, not a section.",
];

const EXPECTED = {
  handover: [
    "1. Per area: what someone taking over must know first (entry points, invariants, the risky paths), and where it is written down today, if anywhere.",
    "2. A handover order: which area first and why (hotspots and weak health before quiet code).",
    "3. For each area, a concrete session the leaver and the suggested person can run (what to walk through, which change to pair on), or the reading list if the leaver is already gone.",
    "4. The documentation and tests that would make the handover stick, as a short checklist.",
  ],
  spread: [
    "1. Per area: what only the holder knows today (entry points, invariants, the risky paths), and where it is written down, if anywhere.",
    "2. Who should learn it and how: the suggested person, a first change they could make with the holder reviewing, and what to read before it.",
    "3. The documentation and tests that would let a second person review changes here with confidence, as a short checklist.",
  ],
} as const;

function fileLine(f: HandoverPromptFile): string {
  const marks = [f.isHotspot ? "hotspot" : null, f.lowHealth ? "weak health" : null].filter(Boolean);
  return `\`${f.path}\` (${Math.round(f.share * 100)}% of commits${marks.length ? `, ${marks.join(", ")}` : ""})`;
}

function areaSection(area: HandoverPromptArea, purpose: HandoverPurpose): string {
  const shown = area.files.slice(0, MAX_FILES_PER_AREA);
  const more = area.files.length - shown.length;
  const who = purpose === "handover" ? "Could take over" : "Could learn it";
  return joinSections([
    `\n### \`${area.dir || "(repository root)"}\` (${area.files.length} file${pluralS(area.files.length)})`,
    area.suggested ? `${who}: ${area.suggested}` : "",
    bulletList([...shown.map(fileLine), more > 0 ? `…and ${more} more in this directory` : null]),
  ]);
}

export function buildHandoverPrompt({
  purpose,
  people,
  areas,
  flavor = "generic",
  repoName,
  threshold = 0.5,
  spreadReason = "each one is a hotspot or in a weak health band",
}: BuildHandoverPromptOptions): string {
  const shown = areas.slice(0, MAX_AREAS);
  const hidden = areas.length - shown.length;
  const files = areas.reduce((s, a) => s + a.files.length, 0);
  const names = namePeople(people);
  const heading =
    purpose === "handover"
      ? `## Knowledge handover from ${names}${repoSuffix(repoName)}`
      : `## Spread knowledge held only by ${names}${repoSuffix(repoName)}`;
  const situation =
    purpose === "handover"
      ? `Without ${names}, the people who stay would hold less than ${Math.round(threshold * 100)}% of the commits behind these ${files} file${pluralS(files)}.`
      : `One person made at least 80% of the commits behind each of these ${files} file${pluralS(files)} (${names}), and ${spreadReason}.`;
  // joinSections drops "" entries, so each section carries its own blank line.
  return joinSections([
    preamble(flavor, PARTS),
    `\n${heading}`,
    `\n${situation}`,
    ...shown.map((a) => areaSection(a, purpose)),
    hidden > 0 ? `\n…and ${hidden} more director${hidden === 1 ? "y" : "ies"}; plan them after the above.` : "",
    `\n${closingSections(CONSTRAINTS, [...EXPECTED[purpose]]).join("\n")}`,
  ]);
}

// ---------------------------------------------------------------------------
// From the derivations to the prompt's areas
// ---------------------------------------------------------------------------

/** Off-boarding areas: the share is what the leavers made. */
export function offboardingPromptAreas(areas: readonly OffboardingArea[]): HandoverPromptArea[] {
  return areas.map((a) => ({
    dir: a.dir,
    suggested: a.inheritor?.person.name ?? null,
    files: a.files.map((f) => ({
      path: f.path,
      isHotspot: f.isHotspot,
      lowHealth: f.lowHealth,
      share: f.lostShare,
    })),
  }));
}

/** Knowledge-risk areas: the share is the holder's, the suggestion the commonest next author. */
export function riskPromptAreas(areas: readonly KnowledgeRiskArea[]): HandoverPromptArea[] {
  return areas.map((a) => ({
    dir: a.dir,
    suggested: mostNamed(a.files.flatMap((f) => (f.second ? [{ person: f.second, nloc: f.nloc }] : [])))
      ?.person.name ?? null,
    files: a.files.map((f) => ({
      path: f.path,
      isHotspot: f.isHotspot,
      lowHealth: f.lowHealth,
      share: f.holder.share,
    })),
  }));
}
