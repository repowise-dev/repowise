/**
 * Every string the Ownership page's views render, so a host can translate
 * them. The views take `labels?: Partial<OwnershipLabels>` and fall back to
 * these English defaults; the web app passes en and zh-CN from its messages.
 * Agent prompt text stays English: it is written for the agent, not the page.
 */

import { AGENT_PROMPT_LABEL, bulkAgentPromptLabel } from "../health/ai-prompt-action";

const s = (n: number, one: string, many: string) => `${n.toLocaleString()} ${n === 1 ? one : many}`;

export interface OwnershipLabels {
  // Shared
  agentPrompt: string;
  /** A group's prompt: "Copy prompt for these 4". */
  promptForGroup: (count: number) => string;
  repositoryRoot: string;
  hotspot: string;
  files: (count: number) => string;
  showAllFiles: (count: number) => string;
  showAllDirectories: (count: number) => string;
  noOtherAuthor: string;
  openInOffboarding: string;

  // Lede
  coverageLabel: string;
  coverageHint: (byLines: boolean, days: number) => string;
  coverageUnit: (byLines: boolean) => string;
  wellShared: string;
  coverageSentence: (pct: string, byLines: boolean, days: number) => string;
  singleHolderSentence: (single: number, files: number) => string;
  noChanges: (days: number) => string;
  spreadTitle: string;
  spreadDescription: (files: number) => string;
  readHotspots: string;
  readHotspotsUnit: (singleOwnerFiles: number) => string;
  readHotspotsWhy: string;
  readQuiet: string;
  readQuietUnit: (count: number) => string;
  readQuietWhy: (days: number) => string;
  readTruck: string;
  readTruckWhy: string;

  // Everyone table
  everyone: string;
  everyoneDescription: string;
  people: (count: number, withAutomation: boolean) => string;
  searchPlaceholder: string;
  searchLabel: string;
  showAutomation: (count: number) => string;
  noMatch: string;
  clearSearch: string;
  noPeople: string;
  noPeopleDescription: string;
  showAllPeople: (count: number, withAutomation: boolean) => string;
  loading: string;

  // Knowledge risks
  risksIntro: (files: number, hotspots: number, lowHealth: number, dirs: number) => string;
  risksCleanTitle: (files: number) => string;
  risksCleanDescription: string;
  risksOneHolder: (name: string) => string;
  risksManyHolders: (holders: number, name: string | null, files: number) => string;
  risksSevere: (count: number) => string;
  risksListLabel: string;

  // Knowledge loss
  lossIntro: (total: number, days: number, shown: number) => string;
  lossCleanTitle: (days: number) => string;
  lossCleanDescription: (people: number, files: number) => string;
  lossCarriedTitle: string;
  lossCarriedDescription: (days: number, quiet: number) => string;
  lossLastCommit: (when: string) => string;
  lossChanging: (changing: number, owned: number) => string;
  lossAtRisk: (files: number, severe: number) => string;
  lossNoneAtRisk: string;
  lossDirectories: (open: boolean, count: number) => string;
  lossListLabel: string;

  // Off-boarding
  leavingLabel: string;
  leavingPicker: string;
  thresholdLabel: (pct: number) => string;
  thresholdHelp: string;
  tryLabel: string;
  pickPrompt: string;
  pickerPlaceholder: string;
  removePerson: (name: string) => string;
  selectedPeople: string;
  unknownPeople: string;
  nothingDependsTitle: (names: string) => string;
  nothingDependsDescription: (threshold: string, files: number, names: string) => string;
  noShareTitle: (names: string, count: number) => string;
  noShareDescription: string;
  resultSentence: (p: {
    names: string;
    files: number;
    threshold: string;
    codeShare: string;
    severe: number;
    kept: number;
  }) => string;
  statAtRisk: string;
  statHotspots: string;
  statHotspotsHint: string;
  statShare: string;
  statShareHint: (nloc: number, files: number) => string;
  statDirectories: string;
  heir: (name: string, files: number, of: number) => string;
  noHeir: string;
  heirSevere: (count: number) => string;
  handoverTitle: string;
  handoverDescription: (files: number, names: string) => string;
  offboardingListLabel: string;
  underOnePercent: string;
}

export const DEFAULT_OWNERSHIP_LABELS: OwnershipLabels = {
  agentPrompt: AGENT_PROMPT_LABEL,
  promptForGroup: bulkAgentPromptLabel,
  repositoryRoot: "(repository root)",
  hotspot: "Hotspot",
  files: (n) => s(n, "file", "files"),
  showAllFiles: (n) => `Show all ${n.toLocaleString()} files`,
  showAllDirectories: (n) => `Show all ${n.toLocaleString()} directories`,
  noOtherAuthor: "no other author",
  openInOffboarding: "Open in off-boarding",

  coverageLabel: "Knowledge coverage",
  coverageHint: (byLines, days) =>
    `Share ${byLines ? "of the code" : "of the files"} changed in the last ${days} days in files two or more people hold (bus factor 2 or more), ${byLines ? "weighted by lines of code" : "by file count"}. Files with no indexed history are left out.`,
  coverageUnit: (byLines) => `${byLines ? "of the code" : "of the files"} changed lately`,
  wellShared: "Well shared",
  coverageSentence: (pct, byLines, days) =>
    `${pct} ${byLines ? "of the code" : "of the files"} changed in the last ${days} days${byLines ? ", by lines," : ""} has two or more people who know it.`,
  singleHolderSentence: (single, files) =>
    `${single.toLocaleString()} of the ${s(files, "changing file", "changing files")} ${single === 1 ? "has" : "have"} one main author; your agent can plan how a second person learns them.`,
  noChanges: (days) =>
    `No code with history changed in the last ${days} days, so there is no moving code to measure knowledge coverage on.`,
  spreadTitle: "Spread the knowledge",
  spreadDescription: (n) => `A plan for a second person to learn ${s(n, "file", "files")} one person holds.`,
  readHotspots: "Single-author hotspots",
  readHotspotsUnit: (n) => `of ${s(n, "single-author file", "single-author files")}`,
  readHotspotsWhy: "Busy files only one person has written. The cheapest place to get a second reader.",
  readQuiet: "Quiet owners of changing code",
  readQuietUnit: (n) => (n === 1 ? "person" : "people"),
  readQuietWhy: (days) => `No commit in ${days} days, yet files they own changed in that time.`,
  readTruck: "Fewest people holding half the owned files",
  readTruckWhy: "The truck factor. Open the off-boarding view to see what depends on them.",

  everyone: "Everyone",
  everyoneDescription:
    "A dot marks sole-owned files held by someone who has stopped committing, so a clean column means nothing to chase.",
  people: (n, all) => (all ? s(n, "contributor", "contributors") : s(n, "person", "people")),
  searchPlaceholder: "Filter by name or email…",
  searchLabel: "Filter people by name or email",
  showAutomation: (n) => `Show ${n.toLocaleString()} bot and agent ${n === 1 ? "identity" : "identities"}`,
  noMatch: "Nobody matches this search",
  clearSearch: "Clear search",
  noPeople: "No people yet",
  noPeopleDescription: "People land with the first git index.",
  showAllPeople: (n, all) => `Show all ${all ? s(n, "contributor", "contributors") : s(n, "person", "people")}`,
  loading: "Loading…",

  risksIntro: (files, hotspots, lowHealth, dirs) =>
    `${s(files, "file has", "files have")} one author and matter right now: ${hotspots.toLocaleString()} ${hotspots === 1 ? "is a hotspot" : "are hotspots"} and ${lowHealth.toLocaleString()} ${lowHealth === 1 ? "is" : "are"} in weak health, across ${s(dirs, "directory", "directories")}. Hand them to your agent for a plan to get a second person reading each one.`,
  risksCleanTitle: (files) =>
    `Every hotspot and weak file has a second author, across ${files.toLocaleString()} files`,
  risksCleanDescription: "Knowledge here is shared: no busy or weak file depends on a single person's history.",
  risksOneHolder: (name) => `${name} wrote nearly all of it`,
  risksManyHolders: (holders, name, files) =>
    `${holders.toLocaleString()} people each hold their own files${name ? `, ${name} the most (${files.toLocaleString()})` : ""}`,
  risksSevere: (n) => `${n.toLocaleString()} ${n === 1 ? "is" : "are"} both a hotspot and in weak health`,
  risksListLabel: "Directories with knowledge held by one person",

  lossIntro: (total, days, shown) =>
    `${s(total, "person has", "people have")} not committed in ${days} days but still own files that changed in that time. Each row shows which of the files that changed lately would have no majority author without them, and who is best placed to pick each directory up.${total > shown ? ` Showing the ${shown.toLocaleString()} with the most changing code.` : ""}`,
  lossCleanTitle: (days) => `Everyone who owns changing code has committed in the last ${days} days`,
  lossCleanDescription: (people, files) =>
    `Across ${s(people, "person", "people")} and ${s(files, "owned file", "owned files")}, no file that changed lately belongs to someone who has stopped committing.`,
  lossCarriedTitle: "Others already carry the code that is still changing",
  lossCarriedDescription: (days, quiet) =>
    `Every file that changed in the last ${days} days keeps a majority of its commits with people who are still here, even without all ${s(quiet, "quiet owner", "quiet owners")}.`,
  lossLastCommit: (when) => `Last commit ${when}.`,
  lossChanging: (changing, owned) =>
    owned === 1
      ? "The one file they own changed lately."
      : `${changing.toLocaleString()} of the ${owned.toLocaleString()} files they own changed lately.`,
  lossAtRisk: (files, severe) =>
    `Without them, ${s(files, "file that changed lately has", "files that changed lately have")} no majority author left${severe > 0 ? `, ${severe.toLocaleString()} of them hotspots or in weak health` : ""}.`,
  lossNoneAtRisk: "The people who stay hold the majority of every changing file they worked on.",
  lossDirectories: (open, n) => `${open ? "Hide" : "Show"} ${s(n, "directory", "directories")}`,
  lossListLabel: "Quiet owners of changing code",

  leavingLabel: "If these people left",
  leavingPicker: "People leaving",
  thresholdLabel: (pct) => `At risk below ${pct}% kept`,
  thresholdHelp: "A file is at risk when the people who stay made less than this share of its commits.",
  tryLabel: "Try:",
  pickPrompt:
    "Pick one or more people to see which files would lose most of the knowledge behind them, and who is best placed to take each directory over. Nothing is saved; the scenario lives in the page address, so you can share it.",
  pickerPlaceholder: "Search by name or email…",
  removePerson: (name) => `Remove ${name}`,
  selectedPeople: "Selected people",
  unknownPeople:
    "None of the selected people appear in the indexed history. They may have committed under a different address; search for them by name.",
  nothingDependsTitle: (names) => `Nothing depends on ${names} alone`,
  nothingDependsDescription: (threshold, files, names) =>
    `The people who stay hold at least ${threshold} of the commits on all ${s(files, "file", "files")} ${names} worked on.`,
  noShareTitle: (names, count) => `${names} ${count === 1 ? "holds" : "hold"} no share of the indexed code`,
  noShareDescription: "No code file lists them among its top five authors.",
  resultSentence: ({ names, files, threshold, codeShare, severe, kept }) =>
    `Without ${names}, ${s(files, "file", "files")} would be left with authors holding under ${threshold} of the commits: ${codeShare} of the code${severe > 0 ? `, ${severe.toLocaleString()} of them hotspots or in weak health` : ""}. ${kept.toLocaleString()} more carry some of their history but keep at least ${threshold} with the people who stay.`,
  statAtRisk: "Files at risk",
  statHotspots: "Hotspots among them",
  statHotspotsHint: "At-risk files in the repository's high-churn band",
  statShare: "Share of code",
  statShareHint: (nloc, files) =>
    `Lines of code in at-risk files, of ${nloc.toLocaleString()} across ${s(files, "file", "files")}`,
  statDirectories: "Directories",
  heir: (name, files, of) =>
    `${name} could take over: the strongest remaining author on ${files.toLocaleString()} of ${of.toLocaleString()}`,
  noHeir: "Nobody who stays is among these files' main authors",
  heirSevere: (n) => `${n.toLocaleString()} ${n === 1 ? "is a hotspot or in" : "are hotspots or in"} weak health`,
  handoverTitle: "Plan the handover",
  handoverDescription: (files, names) => `A handover plan for ${s(files, "file", "files")} that depend on ${names}.`,
  offboardingListLabel: "Directories that would lose their majority author",
  underOnePercent: "under 1%",
};

/** The defaults with a host's overrides on top. */
export function ownershipLabels(overrides?: Partial<OwnershipLabels>): OwnershipLabels {
  return overrides ? { ...DEFAULT_OWNERSHIP_LABELS, ...overrides } : DEFAULT_OWNERSHIP_LABELS;
}
