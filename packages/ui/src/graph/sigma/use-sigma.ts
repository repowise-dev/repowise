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
import { resolveToken, useCommunityFamilies, useThemeVersion } from "../../shared/use-theme-tokens";

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
 * Hub/core disc-label drawer. Hub/core labels render centered *inside* the disc
 * (ROBOTICS-style) with a soft halo ring in the family hue; everything else
 * falls through to Sigma's stock side-label drawer.
 *
 * Takes no palette: every colour it paints comes off the node itself (the halo
 * from `haloColor`, the ink from `labelInk`), both resolved by the colour pass.
 * That is what lets it survive a theme flip without being rebuilt.
 */
function makeDrawNodeLabel(
  drawDisc: typeof drawDiscNodeLabel,
): NodeLabelDrawingFunction {
  return (context, data, settings) => {
    const extra = data as unknown as Record<string, unknown>;
    const kind = extra.nodeType as string | undefined;
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

    const label = data.label;
    if (!label) return;

    // Fit the uppercase label inside the disc; shrink for long names.
    const font = settings.labelFont || "JetBrains Mono, monospace";
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
function makeDrawNodeHover(theme: VizPalette): NodeLabelDrawingFunction {
  return (context, data, settings) => {
    const label = data.label;
    if (!label) return;

    const extra = data as Record<string, unknown>;
    const fullPath = (extra.fullPath as string) ?? undefined;

    // Hub/module tooltip: a small surface card. First disclosure layer —
    // headline stats only; the full detail lives in the inspection panel.
    if (extra.nodeType === "hub" || extra.nodeType === "module") {
      const font = settings.labelFont || "JetBrains Mono, monospace";
      const docPct = Math.round(((extra.docCoveragePct as number) ?? 0) * 100);
      const lines: string[] = [];
      if (extra.nodeType === "hub") {
        const members = (extra.memberCount as number) ?? 0;
        lines.push(`${members} file${members === 1 ? "" : "s"} · ${docPct}% documented`);
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
  };
}

// Read by `dimColor`, which runs inside Sigma's node reducer — outside React,
// so it cannot take the palette as an argument. Kept in sync by the theme
// effect, which also drops the color caches keyed to the previous plane.
let activeBg: readonly [number, number, number] = [14, 14, 15];

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
  const communityFamilies = useCommunityFamilies();

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

  // Sync interaction state refs (no color work here — that's in the graph effect)
  useEffect(() => {
    selectedRef.current = options.selectedNodeId;
    highlightedPathRef.current = options.highlightedPath;
    highlightedEdgesRef.current = options.highlightedEdges;
    searchDimmedRef.current = options.searchDimmedNodes;
    communityDimmedRef.current = options.communityDimmedNodes;
    expandDimmedRef.current = options.expandDimmedNodes ?? null;
    hiddenNodesRef.current = options.hiddenNodes;
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
  }, [options.visibleEdgeTypes, options.graph]);

  // Pre-apply node colors on the graphology graph (batch: 1 event instead of N)
  useEffect(() => {
    const graph = options.graph;
    if (!graph || graph.order === 0) return;
    const cm = options.colorMode;
    const coreColor = resolveToken("--color-bg-inset", "#141415");
    graph.updateEachNodeAttributes(
      (_node, attrs) => {
        let color: string;
        // Constellation kinds are always family-colored (hub hue) regardless of
        // the active colorMode — the radial view *is* the community view. The
        // repo-core is a dark plum disc; its halo borrows the soft canvas dot.
        if (attrs.nodeType === "hub") {
          const family = communityFamilies(attrs.communityId);
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
  useEffect(() => {
    const graph = options.graph;
    if (!graph || graph.size === 0) return;
    const edge = viz.edge;
    graph.updateEachEdgeAttributes(
      (_edgeKey, attrs) => {
        const color = edge[attrs.edgeKind] ?? edge.import;
        if (attrs.color === color) return attrs;
        return { ...attrs, color };
      },
      { attributes: ["color"] },
    );
  }, [options.graph, viz]);

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
    sigma.setSetting("defaultDrawNodeHover", makeDrawNodeHover(viz));
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

        // Hub/core disc labels + hover tooltips. The label drawer is
        // theme-independent (it reads colours off the node) and is built once.
        // The hover drawer paints from the palette, so the theme effect re-sets
        // it on light/dark toggle without a remount; it is read through the ref
        // here because this effect depends only on the container, which would
        // otherwise leave its `viz` closure stale.
        defaultDrawNodeLabel: makeDrawNodeLabel(drawDiscNodeLabel),
        defaultDrawNodeHover: makeDrawNodeHover(vizRef.current),

        // --- nodeReducer: ONLY handles interaction state (selection, search, path) ---
        // Colors and sizes are pre-set on the graphology graph by the effect above.
        nodeReducer: (node, data) => {
          if (data.hidden) return data;

          const hiddenSet = hiddenNodesRef.current;
          if (hiddenSet?.has(node)) return { ...data, hidden: true };

          const selected = selectedRef.current;
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

  // Update graph when it changes
  useEffect(() => {
    const sigma = sigmaRef.current;
    if (!sigma || !options.graph) return;
    graphRef.current = options.graph;
    sigma.setGraph(options.graph);
    sigma.getCamera().animatedReset({ duration: 500 });
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
      { duration: 400 },
    );
  }, []);

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
  };
}
