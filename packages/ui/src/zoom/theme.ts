/**
 * Theme palette for the zoom canvas.
 *
 * Canvas 2D needs concrete color strings (it cannot consume `var(--token)`), so
 * we resolve the design-system CSS custom properties to their computed values
 * at runtime via the shared token resolver, and re-resolve on theme switch. No
 * color literals live here (the file is scanned by the no-raw-hex gate): every
 * value comes from a `--color-*` token, and the SSR/no-window fallback is a CSS
 * named color, never a hex string.
 */

import { resolveTokens } from "../shared/use-theme-tokens";

export interface ZoomPalette {
  bg: string;
  /** Faint minor grid-line color painted under the cards (graph-paper backdrop). */
  grid: string;
  /** Slightly firmer major grid line, drawn every few cells for depth. */
  gridStrong: string;
  /** Leaf (file) card fill. */
  nodeFill: string;
  /** Container (folder/group/layer/system) card fill. */
  nodeFillAlt: string;
  /** Hairline card border. */
  nodeBorder: string;
  /** Slightly stronger border for the hovered card. */
  nodeBorderHover: string;
  /** Soft drop-shadow color for card elevation. */
  shadow: string;
  /** Faint ruled-line color giving cards a notebook-paper texture. */
  rule: string;
  /**
   * Translucent wash painted over the shared paper photo so the card keeps its
   * tint and text contrast (mirrors the KG `--kg-card-texture` wash). Its alpha
   * carries the per-theme paper strength, so the same compositing works in light
   * and dark without branching.
   */
  paperWash: string;
  nodeText: string;
  textMuted: string;
  accent: string;
  /** Neutral connector hue (edges are intentionally low-emphasis). */
  edge: string;
  /** Slightly stronger neutral for tightly-coupled relations. */
  edgeStrong: string;
  hotspot: string;
  dead: string;
  entry: string;
  flow: string;
  /** Code-health traffic-light inks, matching the /files treemap bands. */
  healthAlert: string;
  healthWarning: string;
  healthHealthy: string;
  /** Neutral ink for an unscored file/subtree (health is sparse). */
  healthNeutral: string;
}

/** Map each palette slot to its design-system token. */
const TOKEN_SPEC: Record<keyof ZoomPalette, string> = {
  // Paint the canvas in the page background so the map sits on the same surface
  // as the rest of the page (no distinct dark "container" box around it).
  bg: "--color-bg-root",
  grid: "--color-zoom-grid",
  gridStrong: "--color-zoom-grid-strong",
  nodeFill: "--color-zoom-card-fill",
  nodeFillAlt: "--color-zoom-card-fill-2",
  nodeBorder: "--color-zoom-card-border",
  nodeBorderHover: "--color-zoom-card-border-hover",
  shadow: "--color-zoom-card-shadow",
  rule: "--color-zoom-card-rule",
  paperWash: "--color-zoom-card-paper-wash",
  nodeText: "--color-zoom-card-text",
  textMuted: "--color-text-muted",
  accent: "--color-accent-primary",
  edge: "--color-zoom-edge",
  edgeStrong: "--color-zoom-edge-strong",
  hotspot: "--color-risk-high",
  dead: "--color-stale",
  entry: "--color-success",
  flow: "--color-accent-secondary",
  // Same tokens the health tokens.ts `healthInk` maps to, so a zoom card and the
  // /files treemap tile agree on what counts as red/amber/green.
  healthAlert: "--color-error",
  healthWarning: "--color-warning",
  healthHealthy: "--color-success",
  healthNeutral: "--color-text-tertiary",
};

/** CSS named-color fallbacks (lint-safe, only hit before tokens resolve). */
const FALLBACK: ZoomPalette = {
  bg: "white",
  grid: "rgba(0,0,0,0.045)",
  gridStrong: "rgba(0,0,0,0.08)",
  nodeFill: "white",
  nodeFillAlt: "white",
  nodeBorder: "gainsboro",
  nodeBorderHover: "silver",
  shadow: "rgba(0,0,0,0.1)",
  rule: "transparent",
  paperWash: "rgba(255,255,255,0.82)",
  nodeText: "black",
  textMuted: "gray",
  accent: "blue",
  edge: "silver",
  edgeStrong: "gray",
  hotspot: "crimson",
  dead: "gray",
  entry: "green",
  flow: "teal",
  healthAlert: "crimson",
  healthWarning: "orange",
  healthHealthy: "green",
  healthNeutral: "gray",
};

/** Resolve the live palette from the document theme. Call on the client only. */
export function resolveZoomPalette(): ZoomPalette {
  const resolved = resolveTokens(TOKEN_SPEC);
  const out = {} as ZoomPalette;
  for (const key in TOKEN_SPEC) {
    const k = key as keyof ZoomPalette;
    out[k] = resolved[k] || FALLBACK[k];
  }
  return out;
}
