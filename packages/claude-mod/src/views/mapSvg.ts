/**
 * The map for the desktop app, which draws SVG and not terminal cells: the
 * same layout and the same resting frame (bands, Claude's marks, labels),
 * one SVG unit per map pixel, each row's runs of one color drawn as a
 * stroked line. Pure. No animation: blits are terminal only.
 */

import { fit } from "../format";
import { MAP_COPY, mapAlt } from "./copy";
import { box, svg, text, type Node } from "./elements";
import { GROUND, LABEL_FG, MAP_PALETTES, TERMINAL_DEFAULT, type MapLabel, type MapLayout } from "./map";
import { legendView, type MapPaneParts } from "./mapPane";
import { framePixels, type Overlay } from "./overlay";

/** The Svg element's own limit. */
export const SVG_CHARS = 131_072;
/** Caps that keep a busy Django map (200 reads marked) near 80,000 characters, under the limit. */
const DESKTOP_MAX_COLUMNS = 160;
const DESKTOP_MAX_ROWS = 45;

const color = (c: number): string => `#${c.toString(16).padStart(6, "0")}`;
const escape = (s: string): string => s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");

/** The Svg's cell size for a desktop pane: the terminal size, capped. */
export function desktopSize(size: { columns: number; rows: number }): { columns: number; rows: number } {
  return { columns: Math.min(DESKTOP_MAX_COLUMNS, size.columns), rows: Math.min(DESKTOP_MAX_ROWS, size.rows) };
}

// A label sits on its cell: one unit across, two down.
const textAt = (l: MapLabel, fill: number): string => `<text x="${l.col}" y="${2 * l.row + 1.6}" fill="${color(fill)}">${escape(l.text)}</text>`;

/** Folder names, faint (as on the terminal), then the lit files' names; the terminal's own foreground becomes the mist. */
function svgLabels(layout: MapLayout, overlay: Overlay): string {
  const pal = MAP_PALETTES[layout.style.theme];
  const folder = layout.style.health ? LABEL_FG : pal.folder;
  const names = overlay.names.map((n) => textAt(n, n.color === TERMINAL_DEFAULT ? pal.read : n.color));
  return [...layout.labels.map((l) => textAt(l, folder)), ...names].join("");
}

/** The resting frame as SVG: per color, one path of horizontal runs (`M x y.5h n`). */
export function mapSvg(layout: MapLayout, overlay: Overlay): string {
  const { width, height } = layout;
  const { pixels } = framePixels(layout, overlay);
  const runs = new Map<number, string[]>();
  for (let y = 0; y < height; y++) {
    for (let x = 0; x < width; ) {
      const c = pixels[y * width + x] ?? GROUND;
      let end = x + 1;
      while (end < width && pixels[y * width + end] === c) end++;
      if (c !== GROUND) {
        const list = runs.get(c) ?? [];
        list.push(`M${x} ${y}.5h${end - x}`);
        runs.set(c, list);
      }
      x = end;
    }
  }
  const paths = [...runs].map(([c, d]) => `<path stroke="${color(c)}" d="${d.join("")}"/>`).join("");
  return (
    `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${width} ${height}" shape-rendering="crispEdges">` +
    `<g stroke-width="1" fill="none">${paths}</g>` +
    `<g font-family="monospace" font-size="1.7">${svgLabels(layout, overlay)}</g></svg>`
  );
}

/** The desktop pane: the drawing and the same legend as the terminal's; a drawing over the limit says so instead. */
export function svgPaneView(layout: MapLayout, overlay: Overlay, parts: MapPaneParts): Node {
  const source = mapSvg(layout, overlay);
  const picture =
    source.length <= SVG_CHARS
      ? svg(source, mapAlt(layout.drawn.length))
      : text(fit(MAP_COPY.tooLarge, layout.columns), { dimColor: true, wrap: "truncate-end" });
  return box({ key: "lens-map-pane", flexDirection: "column" }, [picture, ...legendView(parts, layout.style, layout.columns)]);
}
