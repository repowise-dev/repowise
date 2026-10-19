/**
 * The words the canvas itself prints (folder chips, the area hover card), so
 * a host can translate them. English by default; the rail's labels extend
 * this set, so one translated object serves both.
 */
export interface MapCanvasLabels {
  /** A folder's chip under the hotspot lens: "12 hot · 6.4". */
  hotspotChip: (count: number, health: string | null) => string;
  /** File count on the hover card. */
  fileCount: (count: number) => string;
  openHint: string;
  /** The own-files slot beside a folder's sub-folders. */
  directFiles: (folder: string) => string;
  notLoaded: string;
  analysedFiles: (analysed: number, loaded: number) => string;
  flaggedFiles: (flagged: number, analysed: number) => string;
  /** A held-out origin's name, plural: "test files", "generated files". */
  originName: (origin: string) => string;
  /** The footer toggle that brings a held group back: "Show test files (1,755)". */
  showHeld: (name: string, count: number) => string;
  hideHeld: (name: string) => string;
}

/** Names for the stored code_origin values the map holds out. */
const ORIGIN_NAMES: Record<string, string> = {
  test: "test files",
  docs_example: "docs examples",
  build: "build files",
  vendored: "vendored files",
  generated: "generated files",
  tooling: "tooling files",
  production: "production files",
};

const n = (v: number) => v.toLocaleString();

export const DEFAULT_MAP_CANVAS_LABELS: MapCanvasLabels = {
  hotspotChip: (count, health) => (health == null ? `${n(count)} hot` : `${n(count)} hot · ${health}`),
  fileCount: (count) => `${n(count)} ${count === 1 ? "file" : "files"}`,
  openHint: "click to open",
  directFiles: (folder) => `Files directly in ${folder}`,
  notLoaded: "Files not loaded yet · zoom in to read them",
  analysedFiles: (analysed, loaded) => `${n(analysed)} of ${n(loaded)} files analysed`,
  flaggedFiles: (flagged, analysed) => `${n(flagged)} of ${n(analysed)} analysed files flagged`,
  originName: (origin) => ORIGIN_NAMES[origin] ?? origin.replace(/_/g, " "),
  showHeld: (name, count) => `Show ${name} (${n(count)})`,
  hideHeld: (name) => `Hide ${name}`,
};
