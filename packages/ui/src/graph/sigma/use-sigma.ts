import { useRef, useEffect, useCallback, useState, useMemo } from "react";
import type Sigma from "sigma";
import type Graph from "graphology";
import type { NodeLabelDrawingFunction, drawDiscNodeLabel } from "sigma/rendering";
import type { SigmaNodeAttributes, SigmaEdgeAttributes } from "./types";
import type { ColorMode } from "../graph-toolbar";
import type { Signal } from "../context";
import {
  LABEL_FONT,
  LABEL_SIZE,
  LABEL_GRID_CELL_SIZE,
  getLabelDensity,
  getLabelRenderedSizeThreshold,
  edgeColorsForTheme,
  type EdgeKind,
  languageColor,
} from "./constants";
import { resolveToken, useThemeVersion } from "../../shared/use-theme-tokens";
import { useGraphCommunityFamilies } from "../community-colors";
import { CORE_NODE_ID, UNCLUSTERED_COMMUNITY_ID } from "./constellation-adapter";
import { getOverviewMeta, type OverviewMeta } from "./files-overview";
import { attachOverviewLayers, type OverviewFocus } from "./overview-layers";

// ---- Color helpers (kept minimal — avoid regex in hot paths) ----

function hexToRgb(hex: string): [number, number, number] {
  const v = parseInt(hex.slice(1), 16);
  return [(v >> 16) & 255, (v >> 8) & 255, v & 255];
}

function rgbToHex(r: number, g: number, b: number): string {
  return "#" + ((1 << 24) | (r << 16) | (g << 8) | b).toString(16).slice(1);
}

/**
 * Parse a resolved token into RGB. Custom properties come back as authored, so
 * in practice this only ever sees `#rrggbb` — but a token that later becomes
 * `#rgb` or an `rgb()` string must not silently yield NaN, which would poison
 * `dimColor` for every node on the canvas. Runs once per theme flip, never in
 * a hot path, so the regex is affordable here.
 */
function parseColorToRgb(
  value: string,
  fallback: [number, number, number],
): [number, number, number] {
  const v = value.trim();
  if (/^#[0-9a-f]{6}$/i.test(v)) return hexToRgb(v);
  if (/^#[0-9a-f]{3}$/i.test(v)) {
    return [
      parseInt(v[1]! + v[1]!, 16),
      parseInt(v[2]! + v[2]!, 16),
      parseInt(v[3]! + v[3]!, 16),
    ];
  }
  const m = v.match(/^rgba?\(\s*(\d+)[\s,]+(\d+)[\s,]+(\d+)/i);
  if (m) return [Number(m[1]), Number(m[2]), Number(m[3])];
  return fallback;
}

/** WCAG relative luminance. */
function relativeLuminance([r, g, b]: [number, number, number]): number {
  const channel = (c: number) => {
    const s = c / 255;
    return s <= 0.03928 ? s / 12.92 : Math.pow((s + 0.055) / 1.055, 2.4);
  };
  return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b);
}

function contrastRatio(a: number, b: number): number {
  return a > b ? (a + 0.05) / (b + 0.05) : (b + 0.05) / (a + 0.05);
}

// Ink for labels painted *inside* a colored disc. Deliberately theme-independent:
// the disc fill is a community hue that does not track light/dark the way body
// text does, so the ink is picked from the fill's own luminance instead of the
// theme. This used to be a single hardcoded dark ink, which measured 5.23-8.86:1
// across all 12 hues in dark mode but failed 4.5:1 on 10 of 12 in LIGHT, where
// the community hues are deep and saturated.
//
// The pair is deliberately a shade wider than the page's text tokens. Picking by
// luminance alone against the softer #1a1320/#faf7f4 pair left four mid-luminance
// hues stranded at 4.32-4.40:1 — close, but short. These clear 4.5:1 on all 12
// hues in BOTH themes (worst pair 4.54:1) while staying warm, where pure
// black/white would read as a foreign element on this palette.
const DISC_INK_DARK = "#0d0910";
const DISC_INK_LIGHT = "#fffdfb";
const DISC_INK_DARK_LUM = relativeLuminance(hexToRgb(DISC_INK_DARK));
const DISC_INK_LIGHT_LUM = relativeLuminance(hexToRgb(DISC_INK_LIGHT));
const discInkCache = new Map<string, string>();

/** Pick whichever ink contrasts better against the disc fill. */
function discInkFor(fill: string): string {
  const cached = discInkCache.get(fill);
  if (cached) return cached;
  const lum = relativeLuminance(parseColorToRgb(fill, [128, 128, 128]));
  const ink =
    contrastRatio(lum, DISC_INK_DARK_LUM) >= contrastRatio(lum, DISC_INK_LIGHT_LUM)
      ? DISC_INK_DARK
      : DISC_INK_LIGHT;
  discInkCache.set(fill, ink);
  return ink;
}

/**
 * A label on a plate of the canvas colour, so it reads over clusters, bands
 * and edges without a halo. Ink is the primary text token: over a plate of
 * the plane at 0.85 it clears AA in both themes (the plane is the one colour
 * the text tokens are tuned against).
 */
function drawPlatedLabel(
  context: CanvasRenderingContext2D,
  label: string,
  x: number,
  y: number,
  align: "left" | "center",
  font: string,
  avoidOverlap = false,
): void {
  context.font = font;
  const w = context.measureText(label).width;
  const left = align === "center" ? x - w / 2 : x;
  if (avoidOverlap && !reserveLabel(left - 4, y - 9, w + 8, 18)) return;
  context.fillStyle = `rgba(${activeBg[0]},${activeBg[1]},${activeBg[2]},0.85)`;
  context.beginPath();
  context.roundRect(left - 4, y - 9, w + 8, 18, 4);
  context.fill();
  context.fillStyle = activeText;
  context.textAlign = "left";
  context.textBaseline = "middle";
  context.fillText(label, left, y);
}

/**
 * Plates placed so far this frame, flat `[x, y, w, h, ...]`. Sigma's label
 * grid keeps its own picks apart, but a `forceLabel` bypasses the grid, and
 * the overview forces the best-connected file of each community, which often
 * sit side by side. First come keeps its place. Cleared on `beforeRender`.
 */
const placedLabels: number[] = [];

function reserveLabel(x: number, y: number, w: number, h: number): boolean {
  for (let i = 0; i < placedLabels.length; i += 4) {
    if (
      x < placedLabels[i]! + placedLabels[i + 2]! &&
      x + w > placedLabels[i]! &&
      y < placedLabels[i + 1]! + placedLabels[i + 3]! &&
      y + h > placedLabels[i + 1]!
    ) {
      return false;
    }
  }
  placedLabels.push(x, y, w, h);
  return true;
}

/**
 * Node label drawer.
 *
 * - File labels sit beside the disc on a plate.
 * - Community hubs carry their member count inside the disc and their name
 *   outside it, in full. The name used to be fitted *inside* by shrinking the
 *   font until it fit, so a long label came out at 7px uppercase.
 * - The repo core keeps its name inside its disc.
 *
 * Takes no palette: the halo and ink come off the node (`haloColor`,
 * `labelInk`, resolved by the colour pass) and the plate and text from the
 * module-level plane, which the theme pass re-points. That is what lets it
 * survive a theme flip without being rebuilt.
 */
function makeDrawNodeLabel(
  drawDisc: typeof drawDiscNodeLabel,
): NodeLabelDrawingFunction {
  return (context, data, settings) => {
    const extra = data as unknown as Record<string, unknown>;
    const kind = extra.nodeType as string | undefined;
    const font = settings.labelFont || "JetBrains Mono, monospace";
    if (kind === "file") {
      if (!data.label) return;
      drawPlatedLabel(context, data.label, data.x + data.size + 5, data.y, "left", `500 ${settings.labelSize || 11}px ${font}`, true);
      return;
    }
    if (kind !== "hub" && kind !== "core") {
      drawDisc(context, data, settings);
      return;
    }

    const size = data.size || 20;

    // Soft 2px halo ring in the family hue (emulated — NodeCircleProgram
    // has no border and @sigma/node-border isn't a dependency).
    const halo = (extra.haloColor as string) || data.color;
    context.beginPath();
    context.arc(data.x, data.y, size + 2.5, 0, Math.PI * 2);
    context.lineWidth = 2;
    context.strokeStyle = halo;
    context.globalAlpha = 0.55;
    context.stroke();
    context.globalAlpha = 1;

    if (kind === "hub") {
      const count = extra.memberCount as number | undefined;
      if (count != null) {
        context.font = `600 ${Math.max(9, Math.min(13, size * 0.5))}px ${font}`;
        context.textAlign = "center";
        context.textBaseline = "middle";
        // See the note on `labelInk` below: resolved from the base fill.
        context.fillStyle = (extra.labelInk as string) || discInkFor(data.color);
        context.fillText(String(count), data.x, data.y);
      }
      if (data.label) {
        // Yields to a name already placed. Hubs arrive largest first, and on
        // a phone-width canvas the discs crowd; the key names every one.
        drawPlatedLabel(context, data.label, data.x, data.y + size + 16, "center", `600 12px ${font}`, true);
      }
      return;
    }

    const label = data.label;
    if (!label) return;

    // The repo core: fit its uppercase name inside the disc.
    let fontSize = Math.max(9, Math.min(13, size * 0.55));
    context.font = `600 ${fontSize}px ${font}`;
    const maxWidth = size * 1.9;
    while (context.measureText(label).width > maxWidth && fontSize > 7) {
      fontSize -= 1;
      context.font = `600 ${fontSize}px ${font}`;
    }
    context.textAlign = "center";
    context.textBaseline = "middle";
    // Pre-resolved from the node's BASE fill by the color pass. Deriving it
    // here from `data.color` would be wrong: `data` is post-reducer, so a
    // dimmed hub would pick ink against its dimmed fill and come out brighter
    // than an undimmed one — dimming would make labels louder, not quieter.
    // The fallback only covers a node the color pass has not reached yet,
    // whose color is therefore still an undimmed placeholder.
    context.fillStyle = (extra.labelInk as string) || discInkFor(data.color);
    context.fillText(label, data.x, data.y);
  };
}

/**
 * Hover tooltip drawer, built per palette. Hubs get a small surface card (member
 * count, doc %, langs); other nodes get a label/path pill. Factored out of the
 * init effect alongside makeDrawNodeLabel so the theme effect can re-set it on
 * light/dark toggle (the closure must NOT capture a stale palette).
 */
function makeDrawNodeHover(
  theme: VizPalette,
  focus: { readonly current: FocusState | null },
  hovered: { readonly current: string | null },
  sigmaRef: { readonly current: Sigma | null },
): NodeLabelDrawingFunction {
  return (context, data, settings) => {
    const label = data.label;
    if (!label) return;

    const extra = data as Record<string, unknown>;
    const fullPath = (extra.fullPath as string) ?? undefined;

    // Sigma calls this for every highlighted node, and in the Files overview
    // a focused file highlights its whole neighbourhood. Those get a name
    // plate (only the best-connected few, so a hub file does not bury itself
    // in text); the card is for the focused or pointed-at file alone.
    const f = focus.current;
    const key = (extra.key as string | undefined) ?? "";
    if (f && key !== f.node && key !== hovered.current) {
      const rank = f.labelled.get(key);
      if (rank === undefined) return;
      const font = `500 ${settings.labelSize || 11}px ${settings.labelFont || "JetBrains Mono, monospace"}`;
      const sigma = sigmaRef.current;
      if (sigma && neighbourLabelBlocked(context, sigma, f, key, rank, data, font)) return;
      drawPlatedLabel(context, label, data.x + data.size + 5, data.y, "left", font);
      return;
    }

    // Hub/module tooltip: a small surface card. First disclosure layer —
    // headline stats only; the full detail lives in the inspection panel.
    if (extra.nodeType === "hub" || extra.nodeType === "module") {
      const font = settings.labelFont || "JetBrains Mono, monospace";
      const docPct = Math.round(((extra.docCoveragePct as number) ?? 0) * 100);
      const lines: string[] = [];
      if (extra.nodeType === "hub") {
        const members = (extra.memberCount as number) ?? 0;
        // The "not grouped" disc carries no coverage figure.
        const coverage =
          typeof extra.docCoveragePct === "number" ? ` · ${docPct}% documented` : "";
        lines.push(`${members} file${members === 1 ? "" : "s"}${coverage}`);
        const langs = ((extra.languages as string[]) ?? []).slice(0, 3).join(", ");
        if (langs) lines.push(langs);
      } else {
        const files = (extra.fileCount as number) ?? 0;
        lines.push(`${files} file${files === 1 ? "" : "s"} · ${docPct}% documented`);
        const hot = (extra.hotspotCount as number) ?? 0;
        const dead = (extra.deadCount as number) ?? 0;
        const issues: string[] = [];
        if (hot > 0) issues.push(`${hot} hotspot${hot === 1 ? "" : "s"}`);
        if (dead > 0) issues.push(`${dead} dead file${dead === 1 ? "" : "s"}`);
        if (issues.length > 0) lines.push(issues.join(" · "));
      }

      const titleSize = (settings.labelSize || 11) + 1;
      const lineSize = 9;
      context.font = `600 ${titleSize}px ${font}`;
      let maxW = context.measureText(label).width;
      context.font = `400 ${lineSize}px ${font}`;
      for (const l of lines) maxW = Math.max(maxW, context.measureText(l).width);

      const padX = 12;
      const padY = 8;
      const gap = 4;
      const w = maxW + padX * 2;
      const h = titleSize + lines.length * (lineSize + gap) + padY * 2;
      const nodeSize = data.size || 20;
      const cx = data.x;
      const cy = data.y - nodeSize - 14 - h / 2;

      context.fillStyle = theme.tooltip;
      context.beginPath();
      context.roundRect(cx - w / 2, cy - h / 2, w, h, 6);
      context.fill();
      context.lineWidth = 1.5;
      context.strokeStyle = data.color || "#6366f1";
      context.stroke();

      context.textAlign = "center";
      context.textBaseline = "top";
      let ty = cy - h / 2 + padY;
      context.fillStyle = theme.text;
      context.font = `600 ${titleSize}px ${font}`;
      context.fillText(label, cx, ty);
      ty += titleSize + gap;
      context.fillStyle = theme.subtitle;
      context.font = `400 ${lineSize}px ${font}`;
      for (const l of lines) {
        context.fillText(l, cx, ty);
        ty += lineSize + gap;
      }

      // Halo emphasis on hover.
      context.beginPath();
      context.arc(data.x, data.y, nodeSize + 4, 0, Math.PI * 2);
      context.strokeStyle = (extra.haloColor as string) || data.color || "#6366f1";
      context.lineWidth = 2.5;
      context.globalAlpha = 0.6;
      context.stroke();
      context.globalAlpha = 1;
      return;
    }

    const primarySize = settings.labelSize || 11;
    const secondarySize = 9;
    const font = settings.labelFont || "JetBrains Mono, monospace";
    context.font = `500 ${primarySize}px ${font}`;
    const labelWidth = context.measureText(label).width;

    let showPath = false;
    let pathWidth = 0;
    if (fullPath && fullPath !== label) {
      context.font = `400 ${secondarySize}px ${font}`;
      pathWidth = context.measureText(fullPath).width;
      showPath = true;
    }

    const nodeSize = data.size || 8;
    const paddingX = 10;
    const paddingY = 5;
    const lineGap = showPath ? 3 : 0;
    const width = Math.max(labelWidth, pathWidth) + paddingX * 2;
    const height =
      primarySize + (showPath ? lineGap + secondarySize : 0) + paddingY * 2;
    const radius = 5;
    const x = data.x;
    const y = data.y - nodeSize - 12 - height / 2;

    context.fillStyle = theme.tooltip;
    context.beginPath();
    context.roundRect(x - width / 2, y - height / 2, width, height, radius);
    context.fill();
    context.lineWidth = 2;
    context.strokeStyle = data.color || "#6366f1";
    context.stroke();

    context.textAlign = "center";
    context.textBaseline = "middle";

    const labelY = showPath ? y - (lineGap + secondarySize) / 2 : y;
    context.fillStyle = theme.text;
    context.font = `500 ${primarySize}px ${font}`;
    context.fillText(label, x, labelY);

    if (showPath) {
      context.fillStyle = theme.subtitle;
      context.font = `400 ${secondarySize}px ${font}`;
      context.fillText(
        fullPath!,
        x,
        labelY + primarySize / 2 + lineGap + secondarySize / 2,
      );
    }

    context.beginPath();
    context.arc(data.x, data.y, nodeSize + 4, 0, Math.PI * 2);
    context.strokeStyle = data.color || "#6366f1";
    context.lineWidth = 2;
    context.globalAlpha = 0.5;
    context.stroke();
    context.globalAlpha = 1;
  };
}

/**
 * Theme-aware viz colors resolved from the live design tokens. Read on the
 * React side (where getComputedStyle works) and keyed to the theme version, so
 * canvas painting tracks light/dark. Mirrors the per-theme THEME_COLORS shape.
 */
interface VizPalette {
  risk: { high: string; medium: string; low: string };
  hotspot: string;
  decision: string;
  label: string;
  pathHighlight: string;
  edge: Record<EdgeKind, string>;
  /** The canvas plane, as RGB. `dimColor` blends toward this. */
  bg: [number, number, number];
  text: string;
  subtitle: string;
  tooltip: string;
  /** A focused file's own dependencies. The link/selection accent, which as a
   *  stroke clears 3:1 on the plane in both themes (the fill tone does not in
   *  light). */
  outgoing: string;
  /** Files that depend on the focused one: the plum second accent. */
  incoming: string;
}

function resolveVizPalette(theme: "light" | "dark"): VizPalette {
  const dark = theme === "dark";
  return {
    risk: {
      high: resolveToken("--color-risk-high", "#b23a2e"),
      medium: resolveToken("--color-risk-medium", "#9a6614"),
      low: resolveToken("--color-risk-low", "#1d8155"),
    },
    hotspot: resolveToken("--color-warning", "#9a6614"),
    decision: resolveToken("--color-warning", "#9a6614"),
    label: resolveToken("--color-text-secondary", dark ? "#a79db3" : "#5e5360"),
    pathHighlight: resolveToken("--color-accent-fill", "#f59520"),
    edge: edgeColorsForTheme(theme),
    // The canvas plane. sigma-canvas paints `--color-bg-root` explicitly in
    // dark and stays transparent in light over a body painted the same token,
    // so this one value is the correct blend target in BOTH themes. It used to
    // be a hardcoded mirror that had drifted: #12121c (blue-black) against a
    // real #0e0e0f, and a cool white against warm #fbf6f1 paper. dimColor
    // blends toward it, so every dimmed node settled lighter and bluer than
    // the canvas and never fully receded — and dimming is how this canvas
    // expresses focus.
    bg: parseColorToRgb(
      resolveToken("--color-bg-root", dark ? "#0e0e0f" : "#fbf6f1"),
      dark ? [14, 14, 15] : [251, 246, 241],
    ),
    text: resolveToken("--color-text-primary", dark ? "#f2f2f3" : "#241b2c"),
    subtitle: resolveToken("--color-text-secondary", dark ? "#b4b4b9" : "#5e5360"),
    tooltip: resolveToken("--color-bg-surface", dark ? "#141416" : "#ffffff"),
    outgoing: resolveToken("--color-accent-primary", dark ? "#f59520" : "#a16215"),
    incoming: resolveToken("--color-accent-secondary", dark ? "#a98fc4" : "#58436c"),
  };
}

// Read by `dimColor`, which runs inside Sigma's node reducer — outside React,
// so it cannot take the palette as an argument. Kept in sync by the theme
// effect, which also drops the color caches keyed to the previous plane.
let activeBg: readonly [number, number, number] = [14, 14, 15];
// Label ink for the plated labels, re-pointed with the plane for the same
// reason: the label drawer runs outside React and is built once.
let activeText = "#f2f2f3";

/**
 * `#rrggbb` at `alpha`, premultiplied. Sigma's WebGL programs blend with
 * ONE / ONE_MINUS_SRC_ALPHA and never premultiply, so a plain `rgba()` adds
 * its full colour and washes toward white on a light canvas. `additive` zeroes
 * the alpha so overlapping strokes add light, which reads as density on the
 * dark plane and would blow out on the light one.
 */
function premultiplied(hex: string, alpha: number, additive = false): string {
  const v = parseInt(hex.slice(1, 7), 16);
  const [r, g, b] = Number.isFinite(v) ? [(v >> 16) & 255, (v >> 8) & 255, v & 255] : [128, 128, 128];
  const k = (c: number) => Math.round(c * alpha);
  return `rgba(${k(r)},${k(g)},${k(b)},${additive ? 0 : alpha})`;
}

/** A focused file in the Files overview and what it reveals. */
interface FocusState extends OverviewFocus {
  /** The file and every neighbour: drawn above the veil. */
  nodes: Set<string>;
  /** Neighbours that get a name plate, by rank (best-connected first). */
  labelled: Map<string, number>;
  /** Measured plate text widths, filled as labels are first drawn. */
  widths: Map<string, number>;
}

function plateRect(
  x: number,
  y: number,
  size: number,
  width: number,
): [number, number, number, number] {
  return [x + size + 1, y - 9, width + 8, 18];
}

function overlaps(a: readonly number[], b: readonly number[]): boolean {
  return a[0]! < b[0]! + b[2]! && a[0]! + a[2]! > b[0]! && a[1]! < b[1]! + b[3]! && a[1]! + a[3]! > b[1]!;
}

/**
 * Whether a neighbour's name plate would land on a better-connected
 * neighbour's plate or on the focused file's card. Decided by rank rather than
 * by draw order: Sigma draws highlighted nodes in set order, and repaints this
 * layer on its own when the pointer moves, so there is no pass to reset a
 * "placed so far" list on. At most 24 plates, so the pairwise check is ~300
 * rectangle tests per repaint.
 */
function neighbourLabelBlocked(
  context: CanvasRenderingContext2D,
  sigma: Sigma,
  f: FocusState,
  key: string,
  rank: number,
  data: { x: number; y: number; size: number; label?: string | null },
  font: string,
): boolean {
  context.font = font;
  const widthOf = (node: string, label: string) => {
    let w = f.widths.get(node);
    if (w === undefined) {
      w = context.measureText(label).width;
      f.widths.set(node, w);
    }
    return w;
  };
  const mine = plateRect(data.x, data.y, data.size, widthOf(key, data.label ?? ""));
  const focusData = sigma.getNodeDisplayData(f.node);
  if (focusData) {
    // The card: centred above the node, as tall as its two lines.
    const p = sigma.framedGraphToViewport(focusData);
    const half = Math.max(90, widthOf(f.node, focusData.label ?? "") / 2 + 12);
    const top = p.y - sigma.scaleSize(focusData.size) - 12 - 36;
    if (overlaps(mine, [p.x - half, top, half * 2, 40])) return true;
  }
  for (const [other, otherRank] of f.labelled) {
    if (otherRank >= rank) continue;
    const d = sigma.getNodeDisplayData(other);
    if (!d || d.hidden) continue;
    const p = sigma.framedGraphToViewport(d);
    if (overlaps(mine, plateRect(p.x, p.y, sigma.scaleSize(d.size), widthOf(other, d.label ?? "")))) {
      return true;
    }
  }
  return false;
}

/** Name plates on a focused neighbourhood, best-connected first. */
const FOCUS_LABELS = 24;

function computeFocus(
  graph: Graph<SigmaNodeAttributes, SigmaEdgeAttributes>,
  node: string,
  visibleKinds: Set<string> | undefined,
): FocusState {
  const nodes = new Set<string>([node]);
  const edges: OverviewFocus["edges"] = [];
  graph.forEachEdge(node, (_e, attrs, source, target) => {
    if (visibleKinds && !visibleKinds.has(attrs.edgeKind)) return;
    edges.push({ source, target, kind: attrs.edgeKind });
    nodes.add(source === node ? target : source);
  });
  const neighbours = [...nodes].filter((n) => n !== node);
  neighbours.sort((a, b) => graph.degree(b) - graph.degree(a) || (a < b ? -1 : 1));
  const labelled = new Map<string, number>();
  neighbours.slice(0, FOCUS_LABELS).forEach((n, i) => labelled.set(n, i));
  return { node, edges, nodes, labelled, widths: new Map() };
}

/** Camera ratio below which the overview shows file-level detail (every file
 *  label the grid allows, and the edges inside each community), and above
 *  which it goes back. Two thresholds, so a zoom resting near the line does
 *  not flip the whole canvas back and forth. */
const NEAR_BELOW = 0.55;
const FAR_ABOVE = 0.65;
/** Canvas width (px) under which overview zoom names communities only. */
const NARROW_CANVAS = 600;

/**
 * Room Sigma leaves around the graph when it frames it. The default 30px was
 * sized for bare discs; the Communities view hangs each name below its disc
 * and the Files overview floats each community's name above its cluster, so
 * both need more or the outermost names clip at the canvas edge.
 */
function stagePaddingFor(graph: Graph<SigmaNodeAttributes, SigmaEdgeAttributes>): number {
  return graph.hasNode(CORE_NODE_ID) || getOverviewMeta(graph) ? 64 : 30;
}

const dimColorCache = new Map<string, string>();
const brightenColorCache = new Map<string, string>();

function clearColorCaches() {
  dimColorCache.clear();
  brightenColorCache.clear();
}

function dimColor(hex: string, amount: number): string {
  const key = hex + amount;
  const cached = dimColorCache.get(key);
  if (cached) return cached;
  const [r, g, b] = hexToRgb(hex);
  const result = rgbToHex(
    Math.round(activeBg[0] + (r - activeBg[0]) * amount),
    Math.round(activeBg[1] + (g - activeBg[1]) * amount),
    Math.round(activeBg[2] + (b - activeBg[2]) * amount),
  );
  dimColorCache.set(key, result);
  return result;
}

function brightenColor(hex: string, factor: number): string {
  const key = hex + factor;
  const cached = brightenColorCache.get(key);
  if (cached) return cached;
  const [r, g, b] = hexToRgb(hex);
  const result = rgbToHex(
    Math.round(r + ((255 - r) * (factor - 1)) / factor),
    Math.round(g + ((255 - g) * (factor - 1)) / factor),
    Math.round(b + ((255 - b) * (factor - 1)) / factor),
  );
  brightenColorCache.set(key, result);
  return result;
}

function desaturateColor(hex: string, amount: number): string {
  const [r, g, b] = hexToRgb(hex);
  const gray = 0.299 * r + 0.587 * g + 0.114 * b;
  return rgbToHex(
    Math.round(r + (gray - r) * amount),
    Math.round(g + (gray - g) * amount),
    Math.round(b + (gray - b) * amount),
  );
}

function tintColor(hex: string, tintHex: string, amount: number): string {
  const [r, g, b] = hexToRgb(hex);
  const [tr, tg, tb] = hexToRgb(tintHex);
  return rgbToHex(
    Math.round(r + (tr - r) * amount),
    Math.round(g + (tg - g) * amount),
    Math.round(b + (tb - b) * amount),
  );
}

export interface UseSigmaOptions {
  container: HTMLDivElement | null;
  graph: Graph<SigmaNodeAttributes, SigmaEdgeAttributes> | null;
  selectedNodeId: string | null;
  highlightedPath: Set<string>;
  highlightedEdges: Set<string>;
  searchDimmedNodes: Set<string> | null;
  communityDimmedNodes: Set<string> | null;
  /** Constellation blossom: non-expanded clusters dimmed to ~35% while a hub
   *  is expanded, so the open cluster reads as foreground. */
  expandDimmedNodes?: Set<string> | null | undefined;
  colorMode: ColorMode;
  activeSignals: Set<Signal>;
  graphTheme: "light" | "dark";
  hiddenNodes?: Set<string> | undefined;
  visibleEdgeTypes?: Set<string> | undefined;
  /** Suppress the camera easings this hook drives. Read live, so a change to
   *  the OS setting takes effect without a remount. */
  reducedMotion?: boolean | undefined;
  /** Community names for the Files overview's cluster labels. Read live. */
  communityLabels?: Map<number, string> | undefined;
  /** Communities the legend has switched on, or null/undefined for all. The
   *  overview's bands and names follow it; the nodes already do, through
   *  {@link communityDimmedNodes}. */
  activeCommunities?: Set<number> | null | undefined;
}

/** A camera position in Sigma's *framed* coordinate space (what
 *  `getNodeDisplayData` returns), not raw graph coordinates. */
export interface CameraPosition {
  x: number;
  y: number;
  ratio: number;
}

export interface UseSigmaReturn {
  sigma: Sigma | null;
  /** Ease the camera onto a node. `ratio` controls the resting zoom (smaller =
   *  closer); defaults to 0.15 (tight, for small file nodes). Pass a larger
   *  ratio for big constellation hubs so the surrounding cluster stays visible. */
  focusNode: (nodeId: string, ratio?: number) => void;
  fitView: () => void;
  zoomIn: () => void;
  zoomOut: () => void;
  /** A camera position framing `nodeId` as it sits *right now*, for handing to
   *  {@link UseSigmaReturn.setEntryCamera} across a graph swap. */
  nodeCamera: (nodeId: string, ratio?: number) => CameraPosition | null;
  /**
   * Seed where the camera *starts* on the next graph swap, consumed once.
   *
   * A graph swap otherwise resets the camera from wherever it happens to be,
   * which throws away the reader's place. Drilling into a community sets this
   * to the hub's own position, so the new scoped graph opens at the point the
   * hub occupied and eases out to frame itself: one movement, not two pages.
   */
  setEntryCamera: (state: CameraPosition | null) => void;
}

export function useSigmaRenderer(options: UseSigmaOptions): UseSigmaReturn {
  // Re-resolve theme tokens (risk / hotspot / edge / label / plane) when the
  // theme flips so the canvas repaints in the active palette. Memoized, not
  // recomputed per render: this was previously the argument to `useRef(...)`,
  // which evaluates on every render and discards all but the first result —
  // seven getComputedStyle reads per render for nothing.
  const themeVersion = useThemeVersion();
  const viz = useMemo(
    () => resolveVizPalette(options.graphTheme),
    [options.graphTheme, themeVersion],
  );
  // Pre-resolves the 12 community families once per theme version. The color
  // pass below used to call the raw `getCommunityFamily` per node, which is two
  // getComputedStyle reads each — ~3,000 synchronous style reads for a
  // 1,500-node load, repeated on every colorMode toggle and theme flip.
  const communityFamilies = useGraphCommunityFamilies();

  // Mirror of `viz` readable from Sigma's reducers, which run outside React.
  const vizRef = useRef<VizPalette>(viz);

  // Point `dimColor` at the new canvas plane during render, not in the effect
  // below. Sigma renders on its own schedule, so a frame driven by mouse or
  // camera motion can land between commit and passive-effect flush; doing this
  // in the effect would let that frame blend toward the previous theme's plane
  // using caches keyed to it. `viz` is memoized, so this fires once per theme
  // change rather than once per render.
  if (activeBg !== viz.bg) {
    activeBg = viz.bg;
    clearColorCaches();
  }
  activeText = viz.text;

  const sigmaRef = useRef<Sigma | null>(null);
  const [sigmaReady, setSigmaReady] = useState<Sigma | null>(null);
  const selectedRef = useRef<string | null>(null);
  const highlightedPathRef = useRef<Set<string>>(new Set());
  const highlightedEdgesRef = useRef<Set<string>>(new Set());
  const searchDimmedRef = useRef<Set<string> | null>(null);
  const communityDimmedRef = useRef<Set<string> | null>(null);
  const expandDimmedRef = useRef<Set<string> | null>(null);
  const hiddenNodesRef = useRef<Set<string> | undefined>(undefined);
  const graphRef = useRef<Graph<
    SigmaNodeAttributes,
    SigmaEdgeAttributes
  > | null>(null);

  // ---- Files overview (see files-overview.ts / overview-layers.ts) ----
  // All of it lives in refs: hover must reach the canvas without a React
  // render, and the reducers and layers run outside React anyway.
  const overviewRef = useRef<OverviewMeta | null>(null);
  const hoveredRef = useRef<string | null>(null);
  const focusRef = useRef<FocusState | null>(null);
  /** "near" once the camera is close enough for file-level detail. */
  const bucketRef = useRef<"far" | "near">("far");
  /** A phone-width canvas: the community names fill it, so at overview zoom
   *  the ranked file names are left out. Updated on Sigma's resize. */
  const narrowRef = useRef(false);
  const visibleEdgeTypesRef = useRef(options.visibleEdgeTypes);
  visibleEdgeTypesRef.current = options.visibleEdgeTypes;
  const communityLabelsRef = useRef(options.communityLabels);
  communityLabelsRef.current = options.communityLabels;
  const activeCommunitiesRef = useRef(options.activeCommunities ?? null);
  activeCommunitiesRef.current = options.activeCommunities ?? null;
  const familiesRef = useRef(communityFamilies);
  familiesRef.current = communityFamilies;

  /**
   * Recompute which file is focused (the selection, else the hovered file)
   * and repaint only what changed. The reducer marks the neighbourhood
   * highlighted, which is what lifts it above the overview's veil, so a hover
   * re-reduces the old and new neighbourhoods and nothing else: O(degree),
   * not O(graph). `partial: false` leaves the repaint to a caller about to do
   * a full refresh anyway.
   */
  const applyFocusRef = useRef((partial: boolean) => {
    const graph = graphRef.current;
    const next =
      overviewRef.current && highlightedPathRef.current.size === 0
        ? (selectedRef.current ?? hoveredRef.current)
        : null;
    const prev = focusRef.current;
    if (partial && (prev?.node ?? null) === next) return;
    focusRef.current =
      next && graph?.hasNode(next) ? computeFocus(graph, next, visibleEdgeTypesRef.current) : null;
    const sigma = sigmaRef.current;
    if (!partial || !sigma) return;
    const changed = new Set<string>(prev?.nodes);
    for (const n of focusRef.current?.nodes ?? []) changed.add(n);
    // Scheduled: a pointer moving from one file straight onto another fires
    // leave and enter in the same event, and that should cost one frame.
    try {
      sigma.refresh({ partialGraph: { nodes: [...changed] }, skipIndexation: true, schedule: true });
    } catch {
      // A node not yet indexed (a graph swap is mid-flight): repaint it all.
      sigma.scheduleRefresh();
    }
  });

  // Sync interaction state refs (no color work here — that's in the graph effect)
  useEffect(() => {
    selectedRef.current = options.selectedNodeId;
    highlightedPathRef.current = options.highlightedPath;
    highlightedEdgesRef.current = options.highlightedEdges;
    searchDimmedRef.current = options.searchDimmedNodes;
    communityDimmedRef.current = options.communityDimmedNodes;
    expandDimmedRef.current = options.expandDimmedNodes ?? null;
    hiddenNodesRef.current = options.hiddenNodes;
    applyFocusRef.current(false);
    sigmaRef.current?.refresh();
  }, [
    options.selectedNodeId,
    options.highlightedPath,
    options.highlightedEdges,
    options.searchDimmedNodes,
    options.communityDimmedNodes,
    options.expandDimmedNodes,
    options.hiddenNodes,
  ]);

  // Pre-hide edges by type on the graphology graph (batch: 1 event instead of N)
  useEffect(() => {
    const graph = options.graph;
    if (!graph || graph.size === 0) return;
    const visibleTypes = options.visibleEdgeTypes;
    graph.updateEachEdgeAttributes(
      (_edge, attrs) => {
        const shouldHide = visibleTypes ? !visibleTypes.has(attrs.edgeKind) : false;
        if (attrs.hidden === shouldHide) return attrs;
        return { ...attrs, hidden: shouldHide };
      },
      { attributes: ["hidden"] },
    );
    // The focused file's revealed edges follow the same toggles.
    applyFocusRef.current(false);
  }, [options.visibleEdgeTypes, options.graph]);

  // Pre-apply node colors on the graphology graph (batch: 1 event instead of N)
  useEffect(() => {
    const graph = options.graph;
    if (!graph || graph.order === 0) return;
    const cm = options.colorMode;
    const coreColor = resolveToken("--color-bg-inset", "#141415");
    const unclusteredColor = resolveToken("--color-text-tertiary", "#7c7c82");
    graph.updateEachNodeAttributes(
      (_node, attrs) => {
        let color: string;
        // Constellation kinds are always family-colored (hub hue) regardless of
        // the active colorMode — the radial view *is* the community view. The
        // repo-core is a dark plum disc; its halo borrows the soft canvas dot.
        if (attrs.nodeType === "hub") {
          // The "not grouped" disc is no family: it is painted neutral.
          const family =
            attrs.communityId === UNCLUSTERED_COMMUNITY_ID
              ? { hub: unclusteredColor, satellite: unclusteredColor }
              : communityFamilies(attrs.communityId);
          color = family.hub;
          const haloColor = family.satellite || family.hub;
          const labelInk = discInkFor(color);
          if (
            attrs.color === color &&
            attrs.haloColor === haloColor &&
            attrs.labelInk === labelInk
          ) {
            return attrs;
          }
          return { ...attrs, color, haloColor, labelInk };
        }
        if (attrs.nodeType === "core") {
          color = coreColor;
          const labelInk = discInkFor(color);
          if (attrs.color === color && attrs.labelInk === labelInk) return attrs;
          return { ...attrs, color, labelInk };
        }
        if (cm === "language") {
          // Modules aggregate many languages and carry none themselves — fall
          // back to the community hue instead of a meaningless "other" gray.
          color =
            attrs.nodeType === "module"
              ? communityFamilies(attrs.communityId).hub
              : languageColor(attrs.language || "other");
        } else {
          // Community. Modules (centroids) get the hub hue; files use the
          // softer satellite tint so leaves recede behind their anchor.
          //
          // A third branch used to paint a "risk" lens from `pagerank * 3`
          // against thresholds of 0.3 and 0.7. PageRank sums to 1 across the
          // graph, so those thresholds are unreachable on any real repo and
          // the lens rendered entirely green. See the `ColorMode` docstring in
          // `graph-toolbar.tsx` for why it is gone rather than re-tuned.
          const family = communityFamilies(attrs.communityId);
          color = attrs.nodeType === "module" ? family.hub : family.satellite;
        }
        // Boundary stubs are context, not content: they sit outside the
        // community being drawn, so they recede before any signal tint.
        if (attrs.isBoundary) color = desaturateColor(color, 0.8);
        if (attrs.isDead) color = desaturateColor(color, 0.6);
        if (attrs.isHotspot) color = tintColor(color, viz.hotspot, 0.4);
        // Decision-anchored files get a subtle warm tint so they're
        // discoverable on the canvas without dominating it.
        if (attrs.hasDecision) color = tintColor(color, viz.decision, 0.25);
        if (attrs.color === color) return attrs;
        return { ...attrs, color };
      },
      { attributes: ["color", "haloColor", "labelInk"] },
    );
  }, [options.colorMode, options.graph, viz, communityFamilies]);

  // Re-color edges by semantic kind for the active theme (canvas can't resolve
  // var()). Build-time colors are placeholders; this is the source of truth.
  //
  // Two readings colour by community instead of by kind, because there the
  // kind is already known and the community is the information:
  //   - the Communities view's aggregated edges (they carry `weight`): the
  //     larger community's hue, opacity by weight;
  //   - the Files overview's within-community edges: the community's own hue,
  //     faint, so a cluster reads as one texture when zoomed in.
  // Both premultiplied; see `premultiplied`.
  useEffect(() => {
    const graph = options.graph;
    if (!graph || graph.size === 0) return;
    const edge = viz.edge;
    const overview = !!getOverviewMeta(graph);
    const dark = options.graphTheme === "dark";
    graph.updateEachEdgeAttributes(
      (_edgeKey, attrs, _s, _t, sa, ta) => {
        let color: string;
        if (attrs.weight !== undefined) {
          const big = (sa.memberCount ?? 0) >= (ta.memberCount ?? 0) ? sa : ta;
          color = premultiplied(communityFamilies(big.communityId).hub, 0.22 + 0.6 * attrs.weight);
        } else if (overview && attrs.edgeKind === "internal") {
          color = premultiplied(communityFamilies(sa.communityId).hub, dark ? 0.22 : 0.3, dark);
        } else {
          color = edge[attrs.edgeKind] ?? edge.internal;
        }
        if (attrs.color === color) return attrs;
        return { ...attrs, color };
      },
      { attributes: ["color"] },
    );
  }, [options.graph, viz, communityFamilies, options.graphTheme]);

  // Theme flip: publish the new palette to the reducers, re-point the canvas
  // plane that `dimColor` blends toward (dropping the color caches keyed to the
  // old plane), and rebuild the disc-label + hover drawers, whose closures
  // capture palette colors and would otherwise stay pinned to the mount-time
  // theme until remount.
  useEffect(() => {
    vizRef.current = viz;
    const sigma = sigmaRef.current;
    if (!sigma) return;
    sigma.setSetting("labelColor", { color: viz.label });
    // Only the hover drawer needs rebuilding: it paints the tooltip card from
    // the palette. The disc-label drawer takes its colours off the node and is
    // theme-independent, so it is built once at init.
    sigma.setSetting("defaultDrawNodeHover", makeDrawNodeHover(viz, focusRef, hoveredRef, sigmaRef));
    sigma.refresh();
  }, [viz]);

  // Initialize Sigma (dynamic import to avoid SSR WebGL crash)
  useEffect(() => {
    const container = options.container;
    if (!container) return;

    let cancelled = false;
    let sigmaInstance: Sigma | null = null;

    (async () => {
      const [{ default: SigmaConstructor }, edgeCurveModule, sigmaRendering, graphologyModule] =
        await Promise.all([
          import("sigma"),
          import("@sigma/edge-curve"),
          import("sigma/rendering"),
          import("graphology"),
        ]);
      const EdgeCurveProgram = edgeCurveModule.default;
      const EdgeCurvedArrowProgram = edgeCurveModule.EdgeCurvedArrowProgram;
      const EdgeLineProgram = sigmaRendering.EdgeLineProgram;
      const EdgeArrowProgram = sigmaRendering.EdgeArrowProgram;
      const drawDiscNodeLabel = sigmaRendering.drawDiscNodeLabel;

      if (cancelled) return;

      const graph =
        options.graph ?? new graphologyModule.default() as Graph<SigmaNodeAttributes, SigmaEdgeAttributes>;
      graphRef.current = options.graph;
      overviewRef.current = getOverviewMeta(options.graph);
      // A selection can arrive (a `?node=` link) before this async init lands,
      // when there was no overview to focus it in yet.
      applyFocusRef.current(false);

      const sigma = new SigmaConstructor(graph, container, {
        renderLabels: true,
        labelFont: LABEL_FONT,
        labelSize: LABEL_SIZE,
        labelDensity: getLabelDensity(graph.order),
        labelGridCellSize: LABEL_GRID_CELL_SIZE,
        labelRenderedSizeThreshold: getLabelRenderedSizeThreshold(graph.order),
        labelColor: { color: vizRef.current.label },
        defaultNodeColor: "#6b7280",
        defaultEdgeColor: "#2a2a3a",
        defaultEdgeType: "curved",
        edgeProgramClasses: {
          curved: EdgeCurveProgram,
          curvedArrow: EdgeCurvedArrowProgram,
          arrow: EdgeArrowProgram,
          line: EdgeLineProgram,
        },
        minCameraRatio: 0.002,
        maxCameraRatio: 50,
        hideEdgesOnMove: true,
        // Labels are a canvas2d pass that Sigma re-runs on every camera frame,
        // and at this graph size it is the dominant cost of a pan or a zoom —
        // WebGL draws the nodes almost for free by comparison. With this set,
        // dragging and zooming skip renderLabels/renderEdgeLabels/
        // renderHighlightedNodes entirely and paint nodes only; everything
        // comes back on the first settled frame. Pairs with hideEdgesOnMove
        // above, which already does the same for the edge programs.
        hideLabelsOnMove: true,
        zIndex: true,
        stagePadding: stagePaddingFor(graph),

        // Hub/core disc labels + hover tooltips. The label drawer is
        // theme-independent (it reads colours off the node) and is built once.
        // The hover drawer paints from the palette, so the theme effect re-sets
        // it on light/dark toggle without a remount; it is read through the ref
        // here because this effect depends only on the container, which would
        // otherwise leave its `viz` closure stale.
        defaultDrawNodeLabel: makeDrawNodeLabel(drawDiscNodeLabel),
        defaultDrawNodeHover: makeDrawNodeHover(vizRef.current, focusRef, hoveredRef, sigmaRef),

        // --- nodeReducer: ONLY handles interaction state (selection, search, path) ---
        // Colors and sizes are pre-set on the graphology graph by the effect above.
        nodeReducer: (node, data) => {
          if (data.hidden) return data;

          const hiddenSet = hiddenNodesRef.current;
          if (hiddenSet?.has(node)) return { ...data, hidden: true };

          // Files overview. The copy Sigma hands the reducer is its own
          // (`addNode` shallow-copies the attributes first), so it is edited
          // in place rather than spread: this runs for every node on a full
          // refresh.
          const overview = overviewRef.current !== null;
          if (overview && data.nodeType === "file") {
            const focus = focusRef.current;
            if (focus?.nodes.has(node)) {
              // Highlighted is what draws it above the veil (Sigma's hover
              // layers); the dimming of everything else is the veil itself.
              data.highlighted = true;
              data.size = (data.size || 6) * (node === focus.node ? 1.7 : 1.2);
              data.zIndex = 2;
            } else if (bucketRef.current === "far" && (!data.forceLabel || narrowRef.current)) {
              // Far out, only the ranked files are named.
              data.label = "";
            }
          }

          // In the overview the selection is drawn by the focus above, not by
          // dimming every other node.
          const selected = overview ? null : selectedRef.current;
          const pathNodes = highlightedPathRef.current;
          const searchDimmed = searchDimmedRef.current;
          const communityDimmed = communityDimmedRef.current;
          const expandDimmed = expandDimmedRef.current;

          // Fast path: nothing active — return data unchanged, zero allocation
          if (
            !selected &&
            pathNodes.size === 0 &&
            !searchDimmed &&
            !communityDimmed &&
            !expandDimmed
          ) {
            return data;
          }

          if (searchDimmed?.has(node)) {
            return { ...data, color: dimColor(data.color, 0.12), size: (data.size || 6) * 0.5, zIndex: 0 };
          }

          // Blossom dim: other clusters recede to ~35% (size unchanged so the
          // unexpanded constellation stays legible underneath).
          if (expandDimmed?.has(node)) {
            return { ...data, color: dimColor(data.color, 0.35), zIndex: 0 };
          }

          if (communityDimmed?.has(node)) {
            return { ...data, color: dimColor(data.color, 0.1), size: (data.size || 6) * 0.5, zIndex: 0 };
          }

          if (pathNodes.size > 0) {
            if (pathNodes.has(node)) {
              return { ...data, zIndex: 2, highlighted: true };
            }
            return { ...data, color: dimColor(data.color, 0.15), size: (data.size || 6) * 0.5, zIndex: 0 };
          }

          if (selected) {
            const graph = graphRef.current;
            if (graph) {
              if (node === selected) {
                return { ...data, size: (data.size || 6) * 1.8, zIndex: 2, highlighted: true };
              }
              if (graph.hasEdge(node, selected) || graph.hasEdge(selected, node)) {
                return { ...data, size: (data.size || 6) * 1.3, zIndex: 1 };
              }
              return { ...data, color: dimColor(data.color, 0.25), size: (data.size || 6) * 0.6, zIndex: 0 };
            }
          }

          return data;
        },

        // --- edgeReducer: interaction state only ---
        // Edge visibility by type is pre-set on the graph. No idle dimming.
        edgeReducer: (edge, data) => {
          if (data.hidden) return data;

          const selected = selectedRef.current;
          const pathEdges = highlightedEdgesRef.current;
          const pathNodes = highlightedPathRef.current;

          // Files overview: cross-community structure is the bands, and a
          // focused file's own edges are drawn on the focus layer with their
          // direction. What the WebGL layer keeps is the texture inside each
          // community, once the camera is close enough to read it, and a
          // traced path.
          if (overviewRef.current) {
            if (pathEdges.size > 0 && pathEdges.has(edge)) {
              data.color = vizRef.current.pathHighlight;
              data.size = 3;
              data.zIndex = 2;
              data.type = "arrow";
              return data;
            }
            if (pathEdges.size > 0 || data.edgeKind !== "internal" || bucketRef.current === "far") {
              data.hidden = true;
            }
            return data;
          }

          // Fast path: nothing active — zero allocation
          if (!selected && pathEdges.size === 0) return data;

          const graph = graphRef.current;

          if (pathEdges.size > 0) {
            if (pathEdges.has(edge)) {
              return { ...data, color: vizRef.current.pathHighlight, size: Math.max(3, (data.size || 1) * 3), zIndex: 2 };
            }
            if (pathNodes.size > 0 && graph) {
              const [source, target] = graph.extremities(edge);
              if (source && target && pathNodes.has(source) && pathNodes.has(target)) {
                return data;
              }
            }
            return { ...data, color: dimColor(data.color, 0.08), size: 0.2, zIndex: 0 };
          }

          if (selected && graph) {
            const [source, target] = graph.extremities(edge);
            const isConnected = source === selected || target === selected;
            if (isConnected) {
              return { ...data, color: brightenColor(data.color, 1.5), size: Math.max(3, (data.size || 1) * 4), zIndex: 2 };
            }
            return { ...data, color: dimColor(data.color, 0.1), size: 0.3, zIndex: 0 };
          }

          return data;
        },
      });

      sigma.on("beforeRender", () => {
        placedLabels.length = 0;
      });
      const measureNarrow = () => {
        const narrow = sigma.getDimensions().width < NARROW_CANVAS;
        if (narrow === narrowRef.current) return;
        narrowRef.current = narrow;
        if (overviewRef.current) sigma.refresh({ schedule: true });
      };
      measureNarrow();
      sigma.on("resize", measureNarrow);

      // Files overview: hover focuses a file, and the zoom bucket decides
      // how much file-level detail is drawn. Neither touches React.
      sigma.on("enterNode", ({ node }) => {
        if (!overviewRef.current) return;
        if (graphRef.current?.getNodeAttribute(node, "nodeType") !== "file") return;
        hoveredRef.current = node;
        applyFocusRef.current(true);
      });
      sigma.on("leaveNode", () => {
        if (hoveredRef.current === null) return;
        hoveredRef.current = null;
        applyFocusRef.current(true);
      });
      sigma.getCamera().on("updated", (state) => {
        // The zero-length (reduced-motion) animation reports one NaN frame.
        if (!Number.isFinite(state.ratio)) return;
        const current = bucketRef.current;
        const bucket =
          current === "far" ? (state.ratio < NEAR_BELOW ? "near" : "far")
            : state.ratio > FAR_ABOVE ? "far" : "near";
        if (bucket === current) return;
        bucketRef.current = bucket;
        // Crossing the line changes which labels and edges exist, once.
        if (overviewRef.current) sigma.refresh({ schedule: true });
      });

      const detachLayers = attachOverviewLayers(sigma, () => {
        const meta = overviewRef.current;
        if (!meta) return null;
        const v = vizRef.current;
        const labels = communityLabelsRef.current;
        const kinds = visibleEdgeTypesRef.current;
        return {
          meta,
          family: familiesRef.current,
          labelFor: (cid) => labels?.get(cid) ?? `Community ${cid}`,
          palette: {
            bg: v.bg,
            text: v.text,
            subtitle: v.subtitle,
            outgoing: v.outgoing,
            incoming: v.incoming,
            dark: v.bg[0] + v.bg[1] + v.bg[2] < 384,
          },
          focus: focusRef.current,
          showBands: !kinds || kinds.has("crossCommunity"),
          activeCommunities: activeCommunitiesRef.current,
        };
      }, (x, y, w, h) => {
        // Registered after the reset above, so this frame's names go in first.
        placedLabels.push(x, y, w, h);
      });
      sigma.once("kill", detachLayers);

      sigmaInstance = sigma;
      sigmaRef.current = sigma;
      setSigmaReady(sigma);
    })();

    return () => {
      cancelled = true;
      if (sigmaInstance) {
        sigmaInstance.kill();
        sigmaInstance = null;
      }
      sigmaRef.current = null;
      setSigmaReady(null);
    };
  }, [options.container]);

  // Where the camera should *start* on the next graph swap, consumed once.
  const entryCameraRef = useRef<CameraPosition | null>(null);
  const setEntryCamera = useCallback((state: CameraPosition | null) => {
    entryCameraRef.current = state;
  }, []);
  // Read at swap time rather than closed over, so the deps below stay `[graph]`
  // and a settings change never re-swaps the graph.
  const reducedMotionRef = useRef(!!options.reducedMotion);
  reducedMotionRef.current = !!options.reducedMotion;

  // Update graph when it changes
  useEffect(() => {
    const sigma = sigmaRef.current;
    if (!sigma || !options.graph) return;
    graphRef.current = options.graph;
    overviewRef.current = getOverviewMeta(options.graph);
    hoveredRef.current = null;
    focusRef.current = null;
    applyFocusRef.current(false);
    sigma.setSetting("stagePadding", stagePaddingFor(options.graph));
    sigma.setGraph(options.graph);
    const camera = sigma.getCamera();
    const entry = entryCameraRef.current;
    entryCameraRef.current = null;
    // Reduced motion still resets — it is the *travel* that is suppressed, not
    // the destination, and skipping the reset would leave the new graph framed
    // by the old one's camera.
    if (reducedMotionRef.current) {
      camera.animatedReset({ duration: 0 });
      return;
    }
    if (entry) camera.setState({ x: entry.x, y: entry.y, ratio: entry.ratio, angle: 0 });
    camera.animatedReset({ duration: 500 });
  }, [options.graph]);

  // Label culling is a function of graph size, but the init effect above depends
  // only on the container — so a scope switch or a "load more" step to a much
  // larger graph would otherwise keep the mount-time values and label-spam the
  // canvas. `sigmaReady` is in the deps so this also applies once the async init
  // resolves. `setSetting` has no equality check of its own — it revalidates and
  // schedules a refresh on every call — but this only fires when the graph
  // identity actually changes, and the graph-swap effect above already refreshes
  // and resets the camera, so the extra schedule coalesces into that frame.
  useEffect(() => {
    const sigma = sigmaRef.current;
    const graph = options.graph;
    if (!sigma || !graph) return;
    sigma.setSetting("labelDensity", getLabelDensity(graph.order));
    sigma.setSetting(
      "labelRenderedSizeThreshold",
      getLabelRenderedSizeThreshold(graph.order),
    );
  }, [options.graph, sigmaReady]);

  const focusNode = useCallback((nodeId: string, ratio = 0.15) => {
    const sigma = sigmaRef.current;
    const graph = graphRef.current;
    if (!sigma || !graph || !graph.hasNode(nodeId)) return;
    // Camera state lives in Sigma's *framed* (normalized) coordinate space, NOT
    // raw graph coords. Raw graph x/y (radial hubs sit hundreds of units from
    // the origin) would fly the camera off into blank canvas. getNodeDisplayData
    // returns the node's position already in the camera's coordinate system.
    const display = sigma.getNodeDisplayData(nodeId);
    if (!display) return;
    sigma.getCamera().animate(
      { x: display.x, y: display.y, ratio },
      { duration: reducedMotionRef.current ? 0 : 400 },
    );
  }, []);

  const nodeCamera = useCallback(
    (nodeId: string, ratio = 0.15): CameraPosition | null => {
      const sigma = sigmaRef.current;
      const graph = graphRef.current;
      if (!sigma || !graph || !graph.hasNode(nodeId)) return null;
      const display = sigma.getNodeDisplayData(nodeId);
      if (!display) return null;
      return { x: display.x, y: display.y, ratio };
    },
    [],
  );

  const fitView = useCallback(() => {
    sigmaRef.current?.getCamera().animatedReset({ duration: 300 });
  }, []);

  const zoomIn = useCallback(() => {
    sigmaRef.current?.getCamera().animatedZoom({ duration: 200 });
  }, []);

  const zoomOut = useCallback(() => {
    sigmaRef.current?.getCamera().animatedUnzoom({ duration: 200 });
  }, []);

  return {
    sigma: sigmaReady,
    focusNode,
    fitView,
    zoomIn,
    zoomOut,
    nodeCamera,
    setEntryCamera,
  };
}
