/*
 * Categorical node-tone palette (C4 / arch diagrams). These are
 * the data-viz counterpart to LANGUAGE_COLORS — literal hex because they feed
 * the static SVG exporter where a CSS var() string can't resolve. Retuned from
 * the old cool navy/teal/purple set to the warm/plum families (mirrors the
 * --color-community-* / --color-accent-secondary spine). Dark plum-tinted
 * fills carry the C4 node body; the hub hue lines the border + kind band. Each
 * pairing keeps light text >= AA on its fill.
 */
export const TONE_STYLES = {
  system:       { bg: "#3a2417", border: "#f59520", band: "#f59520", text: "#ffffff" }, // brand orange
  person:       { bg: "#2c2530", border: "#84778a", band: "#84778a", text: "#ffffff" }, // charcoal mauve
  external:     { bg: "#251f2c", border: "#5e5360", band: "#5e5360", text: "#e7e1f0" }, // muted plum-gray
  container:    { bg: "#3a2417", border: "#d9825f", band: "#d9825f", text: "#ffffff" }, // peach
  component:    { bg: "#2d1f1a", border: "#cf6a55", band: "#cf6a55", text: "#f1ece9" }, // terracotta
  file:         { bg: "#2a1c33", border: "#826aa0", band: "#826aa0", text: "#ffffff" }, // plum
  function:     { bg: "#1f2a18", border: "#6b7a3d", band: "#6b7a3d", text: "#ffffff" }, // olive/sage
  class:        { bg: "#2a1c33", border: "#a98fc4", band: "#a98fc4", text: "#ffffff" }, // plum-300
  config:       { bg: "#33280a", border: "#a8821f", band: "#a8821f", text: "#ffffff" }, // gold
  document:     { bg: "#1a2c2a", border: "#558f89", band: "#558f89", text: "#ffffff" }, // deep teal
  service:      { bg: "#2d1620", border: "#9e5570", band: "#9e5570", text: "#ffffff" }, // wine
  pipeline:     { bg: "#251f2c", border: "#7d659c", band: "#7d659c", text: "#ffffff" }, // plum-soft
  module:       { bg: "#1f2a18", border: "#90a05e", band: "#90a05e", text: "#ffffff" }, // sage-soft
  layerCluster: { bg: "#1a1320", border: "#58436c", band: "#58436c", text: "#f4f1f8" }, // deep plum
  portal:       { bg: "transparent", border: "#8c7f88", band: "#8c7f88", text: "#a79db3" }, // neutral
  concept:      { bg: "#251f2c", border: "#5e5360", band: "#5e5360", text: "#e7e1f0" }, // charcoal mauve
  table:        { bg: "#2d1f1c", border: "#b06a86", band: "#b06a86", text: "#ffffff" }, // dusty rose
  endpoint:     { bg: "#2a2417", border: "#c9a544", band: "#c9a544", text: "#ffffff" }, // gold-soft
  schema:       { bg: "#2d1f1c", border: "#cf93ab", band: "#cf93ab", text: "#ffffff" }, // dusty rose-soft
  resource:     { bg: "#251f2c", border: "#7d659c", band: "#7d659c", text: "#ffffff" }, // plum-soft
} as const;

export type ToneName = keyof typeof TONE_STYLES;
export type ToneStyle = (typeof TONE_STYLES)[ToneName];

export function getTone(name: string): ToneStyle {
  return TONE_STYLES[name as ToneName] ?? TONE_STYLES.external;
}

export const ARCH_NODE_SIZES = {
  file:         { width: 300, height: 140 },
  function:     { width: 260, height: 110 },
  class:        { width: 280, height: 120 },
  config:       { width: 260, height: 110 },
  document:     { width: 260, height: 110 },
  service:      { width: 260, height: 110 },
  pipeline:     { width: 260, height: 110 },
  module:       { width: 280, height: 120 },
  layerCluster: { width: 360, height: 220 },
  portal:       { width: 240, height: 68 },
  concept:      { width: 260, height: 110 },
  table:        { width: 260, height: 110 },
  endpoint:     { width: 260, height: 110 },
  schema:       { width: 260, height: 110 },
  resource:     { width: 260, height: 110 },
  /** Collapsed folder container card (ArchContainerNode, chevron closed). */
  containerCollapsed: { width: 260, height: 64 },
} as const;
