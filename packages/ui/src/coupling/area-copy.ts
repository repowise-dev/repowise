/**
 * Every string the area coupling view prints, with English defaults. The
 * package has no i18n: a host passes translated copy for any key it overrides.
 */

const fmt = (n: number) => n.toLocaleString();
const plural = (n: number, one: string, many: string) => `${fmt(n)} ${n === 1 ? one : many}`;

export const DEFAULT_AREA_COUPLING_COPY = {
  rootLabel: "Repository root",
  ledeLabel: "Hidden coupling",
  ledeUnit: (n: number): string => (n === 1 ? "area pair" : "area pairs"),
  verdict: (n: number) =>
    `${n === 1 ? "This area pair changes" : "These area pairs change"} together although no file in one imports a file in the other, so a change on one side quietly needs one on the other.`,
  scope: (pairs: number, depth: number, testsIncluded: boolean) =>
    `Across ${plural(pairs, "file pair", "file pairs")}, grouped ${depth} ${depth === 1 ? "level" : "levels"} down.` +
    (testsIncluded ? "" : " Test files are left out: a test is expected to change with its code."),
  cleanTitle: (depth: number) =>
    `Every area pair that changes together also has an import link, ${depth} ${depth === 1 ? "level" : "levels"} down`,
  cleanBody: (pairs: number) =>
    pairs > 0
      ? `Checked ${plural(pairs, "file pair", "file pairs")} that change together across areas.`
      : "No file pairs change together across areas under these filters.",
  unchecked:
    "Part of this index predates the dependency check, so it cannot say which areas change together without an import link. Run repowise update to add it.",
  hiddenMark: "No import link between the areas",
  showing: (shown: number, total: number) => `Showing the top ${fmt(shown)} of ${fmt(total)} area pairs.`,
  filePairs: (n: number) => plural(n, "file pair", "file pairs"),
  coChanges: (n: number) => plural(n, "co-change", "co-changes"),
  lastTogether: (date: string) => `last ${date}`,
  unexplained: (n: number, pct: number) =>
    `${plural(n, "file pair", "file pairs")} with no direct import (${pct}%)`,
  allExplained: "All explained",
  promptLabel: "Copy prompt for your agent",
  bulkPromptLabel: (n: number) => `Copy prompt for these ${fmt(n)}`,
  promptTitle: "Decouple prompt",
  promptDescription:
    "A ready-to-paste prompt asking your agent to find why these areas change together with no dependency between them, and to decouple what is accidental.",
  backToAreas: "All area pairs",
  pairHeadline: (unexplained: number, pairs: number) =>
    `${fmt(unexplained)} of ${plural(pairs, "file pair has", "file pairs have")} no direct import.`,
  allFilePairs: "Every file pair",
  browseFilePairs: "Browse every file pair",
  workspaceLink: "Coupling across repositories",
  filters: {
    label: "Filter couplings",
    unexplained: "Unexplained only",
    crossArea: "Across areas only",
    includeTests: "Include tests",
    minSupport: "Shared commits",
    anySupport: "Any",
    atLeast: (n: number) => `${n}+`,
    recency: "Changed together",
    anyTime: "Any time",
    lastDays: (days: number) => (days >= 365 ? "Last year of history" : `Last ${days} days of history`),
    depth: "Group at depth",
    autoDepth: (n: number) => `Auto (${n})`,
  },
};

export type AreaCouplingCopy = typeof DEFAULT_AREA_COUPLING_COPY;

/** Host overrides on top of the defaults; filter copy merges key by key. */
export type AreaCouplingCopyOverrides = Partial<Omit<AreaCouplingCopy, "filters">> & {
  filters?: Partial<AreaCouplingCopy["filters"]>;
};

export function resolveAreaCouplingCopy(over?: AreaCouplingCopyOverrides): AreaCouplingCopy {
  return {
    ...DEFAULT_AREA_COUPLING_COPY,
    ...over,
    filters: { ...DEFAULT_AREA_COUPLING_COPY.filters, ...over?.filters },
  };
}
