/**
 * Flow's two accents, from the brand tokens, and its motifs. Terminals run
 * their own colors (mostly dark, some light), so readable text keeps the
 * terminal's foreground or its dim, never a fixed color, and nothing sets a
 * background. The accents are marks only: plum for decoration and Repowise's
 * marks, amber for Claude's attention, each from the ramp of the terminal's
 * theme (dark unless the session says light). Health colors never appear here.
 * The motifs are fixed shapes that carry no data.
 */

import { BRAND, DARK, LIGHT } from "@repowise-dev/ui/brand";

export interface FlowTheme {
  /** Heather plum: the hills, Repowise's marks, the thistle. */
  plum: string;
  /** Claude's attention: the call in flight, the owl's eyes. */
  amber: string;
}

const DARK_THEME: FlowTheme = { plum: DARK.accentSecondary, amber: BRAND.accent };
/** On a light ground the deeper plum, and the amber darkened for contrast. */
const LIGHT_THEME: FlowTheme = { plum: LIGHT.accentSecondary, amber: BRAND.accentTextLight };

export function flowTheme(light: boolean): FlowTheme {
  return light ? LIGHT_THEME : DARK_THEME;
}

/** Claude Code's `theme` setting, as the two ramps Lens draws with: light for any light theme, else dark. */
export type ThemeName = "dark" | "light";

export function themeOf(value: unknown): ThemeName {
  return typeof value === "string" && value.startsWith("light") ? "light" : "dark";
}

/** One period of the hills along the pane's top. */
const HILLS = "▁▂▃▅▃▂▁▁▂▃▂▁▁▁▂▃▄▃▂▁";
/** The hills mirrored in the water: lower shapes read as a flat shore, higher as reflections. */
const MIRROR: Record<string, string> = { "▁": " ", "▂": "▔", "▃": "▔", "▄": "▀", "▅": "▀" };

export const THISTLE = "⚘";

function repeat(pattern: string, columns: number): string {
  return pattern.repeat(Math.ceil(Math.max(0, columns) / pattern.length)).slice(0, Math.max(0, columns));
}

export function hills(columns: number): string {
  return repeat(HILLS, columns);
}

/** The hills reflected, under a finished turn. */
export function loch(columns: number): string {
  return [...hills(columns)].map((c) => MIRROR[c] ?? " ").join("");
}
