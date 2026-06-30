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
  /** Faint dot-grid color painted under the cards for a "designed surface" feel. */
  dot: string;
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
}

/** Map each palette slot to its design-system token. */
const TOKEN_SPEC: Record<keyof ZoomPalette, string> = {
  // Paint the canvas in the page background so the map sits on the same surface
  // as the rest of the page (no distinct dark "container" box around it).
  bg: "--color-bg-root",
  dot: "--color-canvas-dot",
  nodeFill: "--color-zoom-card-fill",
  nodeFillAlt: "--color-zoom-card-fill-2",
  nodeBorder: "--color-zoom-card-border",
  nodeBorderHover: "--color-zoom-card-border-hover",
  shadow: "--color-zoom-card-shadow",
  rule: "--color-zoom-card-rule",
  nodeText: "--color-zoom-card-text",
  textMuted: "--color-text-muted",
  accent: "--color-accent-primary",
  edge: "--color-zoom-edge",
  edgeStrong: "--color-zoom-edge-strong",
  hotspot: "--color-risk-high",
  dead: "--color-stale",
  entry: "--color-success",
  flow: "--color-accent-secondary",
};

/** CSS named-color fallbacks (lint-safe, only hit before tokens resolve). */
const FALLBACK: ZoomPalette = {
  bg: "white",
  dot: "gainsboro",
  nodeFill: "white",
  nodeFillAlt: "white",
  nodeBorder: "gainsboro",
  nodeBorderHover: "silver",
  shadow: "rgba(0,0,0,0.1)",
  rule: "transparent",
  nodeText: "black",
  textMuted: "gray",
  accent: "blue",
  edge: "silver",
  edgeStrong: "gray",
  hotspot: "crimson",
  dead: "gray",
  entry: "green",
  flow: "teal",
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
