export const TONE_STYLES = {
  system:       { bg: "#1f3a8a", border: "#1e40af", band: "#1e40af", text: "#ffffff" },
  person:       { bg: "#374151", border: "#4b5563", band: "#4b5563", text: "#ffffff" },
  external:     { bg: "#1f2937", border: "#374151", band: "#374151", text: "#e5e7eb" },
  container:    { bg: "#0f3a5f", border: "#1d4ed8", band: "#1d4ed8", text: "#ffffff" },
  component:    { bg: "#0d2a47", border: "#1e3a8a", band: "#1e3a8a", text: "#e5e7eb" },
  file:         { bg: "#0f3a5f", border: "#1a6b8a", band: "#1a6b8a", text: "#ffffff" },
  function:     { bg: "#1a3d2e", border: "#2d7d5a", band: "#2d7d5a", text: "#ffffff" },
  class:        { bg: "#2d1f4a", border: "#6b4fa0", band: "#6b4fa0", text: "#ffffff" },
  config:       { bg: "#3d2f0a", border: "#8a6b1a", band: "#8a6b1a", text: "#ffffff" },
  document:     { bg: "#1a3a5a", border: "#4a8ab0", band: "#4a8ab0", text: "#ffffff" },
  service:      { bg: "#3d1a2f", border: "#8a4a6b", band: "#8a4a6b", text: "#ffffff" },
  pipeline:     { bg: "#2d1f3d", border: "#6b5a8a", band: "#6b5a8a", text: "#ffffff" },
  module:       { bg: "#1f2d1a", border: "#5a7a4a", band: "#5a7a4a", text: "#ffffff" },
  layerCluster: { bg: "#0d1a2d", border: "#1a3a5a", band: "#1a3a5a", text: "#f1f5f9" },
  portal:       { bg: "transparent", border: "#475569", band: "#475569", text: "#94a3b8" },
  concept:      { bg: "#1f2937", border: "#374151", band: "#374151", text: "#e5e7eb" },
  table:        { bg: "#1a2d3d", border: "#3d6b8a", band: "#3d6b8a", text: "#ffffff" },
  endpoint:     { bg: "#2d3d1a", border: "#6b8a3d", band: "#6b8a3d", text: "#ffffff" },
  schema:       { bg: "#1a2d3d", border: "#3d6b8a", band: "#3d6b8a", text: "#ffffff" },
  resource:     { bg: "#2d1f3d", border: "#6b5a8a", band: "#6b5a8a", text: "#ffffff" },
} as const;

export type ToneName = keyof typeof TONE_STYLES;
export type ToneStyle = (typeof TONE_STYLES)[ToneName];

export function getTone(name: string): ToneStyle {
  return TONE_STYLES[name as ToneName] ?? TONE_STYLES.external;
}

export const ARCH_NODE_SIZES = {
  file:         { width: 260, height: 120 },
  function:     { width: 220, height: 90 },
  class:        { width: 240, height: 100 },
  config:       { width: 220, height: 90 },
  document:     { width: 220, height: 90 },
  service:      { width: 220, height: 90 },
  pipeline:     { width: 220, height: 90 },
  module:       { width: 240, height: 100 },
  layerCluster: { width: 300, height: 180 },
  portal:       { width: 220, height: 60 },
  concept:      { width: 220, height: 90 },
  table:        { width: 220, height: 90 },
  endpoint:     { width: 220, height: 90 },
  schema:       { width: 220, height: 90 },
  resource:     { width: 220, height: 90 },
} as const;
