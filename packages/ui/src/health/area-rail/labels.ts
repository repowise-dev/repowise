import { DEFAULT_MAP_CANVAS_LABELS, type MapCanvasLabels } from "../map/canvas-labels";

/** Every string the area map section, its canvas and its rail render, so a host can translate it. */
export interface AreaRailLabels extends MapCanvasLabels {
  mapTitle: string;
  /** Under the title, with the areas API (lazy hierarchy, rail). */
  mapDescription: string;
  /** Under the title, for a flat feed. */
  flatMapDescription: string;
  searchPlaceholder: string;
  mapLoadError: string;
  /** The canvas's accessible name and key help, for the active lens. */
  mapAriaLabel: (lens: string) => string;
  /** What the map and rail cover: nothing held back in. */
  scopeProduction: string;
  /** Every held group brought back. */
  scopeAll: string;
  /** Some held groups brought back: "Production code and test files". */
  scopeWith: (names: string) => string;
  /** Under an area that failed to load while groups are held out. */
  heldOutHint: string;
  /** The lens switcher's menu of further lenses. */
  moreLenses: string;
  /** A lens's name in the switcher; `undefined` keeps the built-in English. */
  lensName?: ((lens: string) => string | undefined) | undefined;
  /** The rail's one filled action: what the N are. */
  promptAction: (count: number) => string;
  repository: string;
  /** Eyebrow over the repository summary and over an area card. */
  repoEyebrow: string;
  areaEyebrow: string;
  files: (count: number) => string;
  /** "+0.42 in 30 days", the window the delta covers. */
  deltaWindow: (delta: string, days: number) => string;
  /** The calm good-news line under an improved score. */
  improved: (delta: string, days: number) => string;
  noDelta: string;
  needsAttention: string;
  subAreas: string;
  improvedMost: string;
  worstFiles: string;
  hotspots: (count: number) => string;
  owners: string;
  ownerShare: (name: string, pct: number) => string;
  busFactorOne: (count: number) => string;
  coverage: string;
  noCoverage: string;
  coverageLine: (pct: number, files: number) => string;
  topFindings: string;
  noFindings: string;
  allClean: (files: number) => string;
  promptTitle: string;
  promptDescription: string;
  findingsLink: string;
  fixFirstLink: string;
  contributorsLink: string;
  backToArea: (name: string) => string;
  loading: string;
  loadError: string;
}

const s = (n: number, one: string, many: string) => `${n.toLocaleString()} ${n === 1 ? one : many}`;

export const DEFAULT_AREA_RAIL_LABELS: AreaRailLabels = {
  ...DEFAULT_MAP_CANVAS_LABELS,
  mapTitle: "Code health map",
  mapDescription:
    "Every folder is a disc sized by lines of code and coloured by the lens. Zoom in and folders open into their sub-folders, then their files. Click a folder to open it, a file to read it in the rail.",
  flatMapDescription:
    "Every file as a node, grouped into its folders and sized by lines of code. The lens re-marks the same field rather than redrawing it. Click a folder to zoom, a file to inspect it.",
  searchPlaceholder: "Find a file in the map…",
  moreLenses: "More",
  heldOutHint:
    "This folder may hold only files the map leaves out by default. Bring them back with the toggles under the map.",
  scopeProduction: "Production code",
  scopeAll: "All code",
  scopeWith: (names) => `Production code and ${names}`,
  mapLoadError: "Couldn't load the map",
  mapAriaLabel: (lens) =>
    `Code health map, ${lens} lens. Arrow keys move between the areas and files inside the selected area, Enter opens one, Backspace goes up a level, Escape returns to the repository, plus and minus zoom.`,
  promptAction: (n) => (n === 1 ? "Copy prompt for the top finding" : `Copy prompt for the top ${n.toLocaleString()} findings`),
  repository: "Repository",
  repoEyebrow: "Repository",
  areaEyebrow: "Area",
  files: (n) => s(n, "file", "files"),
  deltaWindow: (delta, days) => `${delta} in ${days} days`,
  improved: (delta, days) => `Up ${delta} over the last ${days} days. The work here is paying off.`,
  noDelta: "No earlier snapshot to compare with yet.",
  needsAttention: "Needs attention",
  subAreas: "Inside, lowest first",
  improvedMost: "Improved most",
  worstFiles: "Lowest-scoring files",
  hotspots: (n) => `Hotspots · ${n.toLocaleString()}`,
  owners: "Who knows it",
  ownerShare: (name, pct) => `${name} leads ${pct}% of the files`,
  busFactorOne: (n) => `${s(n, "file", "files")} only one person has changed`,
  coverage: "Tests",
  noCoverage: "No coverage report covers this area.",
  coverageLine: (pct, files) => `${pct}% line coverage over ${s(files, "measured file", "measured files")}`,
  topFindings: "Top findings",
  noFindings: "No open findings here.",
  allClean: (files) => `No open findings across ${s(files, "file", "files")}.`,
  promptTitle: "Agent prompt for this area",
  promptDescription: "The area's highest-priority findings, ready to paste into your agent.",
  findingsLink: "Findings here",
  fixFirstLink: "Fix first here",
  contributorsLink: "Contributors here",
  backToArea: (name) => `Back to ${name}`,
  loading: "Loading area",
  loadError: "Couldn't load this area.",
};
