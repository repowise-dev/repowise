/**
 * Canonical brand constants — for surfaces that cannot resolve CSS variables
 * (OG image routes, transactional emails, badge SVGs, terminal output).
 *
 * Values mirror styles/globals.css, the single source of truth for the token
 * system; in-app UI must keep using `var(--color-*)` tokens. This module is
 * deliberately dependency-free (no imports at all) so edge runtimes and email
 * renderers can pull it in without dragging any UI weight.
 */

/** Brand identity. */
export const BRAND = {
  /** Accent orange — the brand fill (CTAs, owl eyes, highlights). */
  accent: "#f59520",
  /** Accent text on light surfaces (darkened for 4.5:1 contrast). */
  accentTextLight: "#a16215",
  /** Brand plum — logo strokes on light surfaces, secondary accent. */
  plum: "#473659",
  /** Plum near-black — primary ink on warm paper. */
  ink: "#241b2c",
  /** Warm paper — the light-mode page background. */
  paper: "#fbf6f1",
  /** Cream — the logo stroke color on dark surfaces. */
  cream: "#fce9dd",
} as const;

/** Light-mode surface/text values (the product default). */
export const LIGHT = {
  bgRoot: "#fbf6f1",
  bgSurface: "#ffffff",
  bgElevated: "#fbf4ee",
  bgInset: "#f4eae1",
  textPrimary: "#241b2c",
  textSecondary: "#5e5360",
  textTertiary: "#8c7f88",
  accentPrimary: "#a16215",
  accentFill: "#f59520",
  accentSecondary: "#58436c",
  success: "#1d8155",
  warning: "#9a6614",
  error: "#b23a2e",
} as const;

/** Dark-mode surface/text values. */
export const DARK = {
  bgRoot: "#17131d",
  bgSurface: "#211b29",
  bgElevated: "#2a2335",
  bgInset: "#110d17",
  textPrimary: "#eeeaf4",
  textSecondary: "#a79db3",
  textTertiary: "#786f84",
  accentPrimary: "#f59520",
  accentFill: "#f59520",
  accentSecondary: "#a98fc4",
  success: "#34d399",
  warning: "#f2a03d",
  error: "#e06a5a",
} as const;

/** Signature gradients (CSS gradient strings) for hero/OG canvases. */
export const GRADIENTS = {
  sunset: "linear-gradient(135deg, #58436c 0%, #f59520 55%, #f7a94d 100%)",
  ember: "linear-gradient(135deg, #f59520 0%, #f7a94d 100%)",
  peach: "linear-gradient(160deg, #ffc4b1 0%, #f59520 100%)",
  warm: "linear-gradient(135deg, #f59520 0%, #ffd9b0 100%)",
} as const;
