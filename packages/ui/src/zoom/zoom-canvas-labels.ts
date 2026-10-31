/**
 * The canvas's accessible names and hover-card copy, with English defaults a
 * host overrides with translated ones (see `ZoomCanvasProps.labels`).
 */

export interface ZoomCanvasLabels {
  /** The canvas's accessible name: what it is and every gesture and key. */
  canvas: string;
  /** The minimap's accessible name. */
  minimap: string;
  /** Hover card: how many relations the card has. */
  relationCount: (count: number) => string;
  /** Hover card: the per-level cap is hiding some of them. */
  capOnMap: (shown: number) => string;
  /** Hover card: how many deployables (C4 containers) a node's files span. */
  containerSpan: (count: number) => string;
  /** External hover card: import edges from the parts in view, files using it repo-wide. */
  externalUsage: (imports: number, files: number) => string;
  /** External hover card: heading over the parts importing it most. */
  externalMostFrom: string;
  /** External hover card: where a double-click goes. */
  externalDive: (name: string) => string;
}

export const DEFAULT_ZOOM_CANVAS_LABELS: ZoomCanvasLabels = {
  canvas:
    "Zoomable system map. Drag to pan, scroll to zoom, double-click a card to dive into it. Keys: plus and minus zoom, 0 fits the whole system, Enter selects the centre card or dives into the selected one, arrow keys pan or move between sibling cards, Escape closes the selection or goes up a level.",
  minimap: "Overview map. Click or drag to move the view.",
  relationCount: (n) => (n === 1 ? "1 relation" : `${n.toLocaleString("en-US")} relations`),
  capOnMap: (shown) => `Showing the ${shown} strongest on the map`,
  externalUsage: (imports, files) =>
    `${imports === 1 ? "1 import" : `${imports.toLocaleString("en-US")} imports`} from here · ${files === 1 ? "1 file" : `${files.toLocaleString("en-US")} files`} in the repo`,
  containerSpan: (n) => `Spans ${n} containers`,
  externalMostFrom: "Imported most by",
  externalDive: (name) => `Double-click to go to ${name}`,
};
