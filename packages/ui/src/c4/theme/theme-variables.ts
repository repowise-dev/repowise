export const THEME = {
  canvas: {
    bg: "var(--color-bg-canvas)",
    dot: "var(--color-canvas-dot, rgba(148,163,184,0.08))",
  },

  surface: {
    elevated: "var(--color-bg-elevated, rgba(17,24,39,0.96))",
    overlay: "var(--color-bg-overlay, rgba(15,23,42,0.92))",
    glass: "var(--color-bg-glass, rgba(17,24,39,0.75))",
    glassBlur: "blur(8px)",
    wash: "var(--color-bg-wash)",
    washHover: "var(--color-bg-wash-hover)",
  },

  border: {
    default: "var(--color-border-default)",
    subtle: "var(--color-border-subtle, rgba(148,163,184,0.12))",
  },

  text: {
    primary: "var(--color-text-primary)",
    secondary: "var(--color-text-secondary)",
    muted: "var(--color-text-muted)",
  },

  accent: {
    primary: "var(--color-accent-primary)",
    muted: "var(--color-accent-muted, rgba(245,149,32,0.2))",
    hover: "var(--color-accent-hover, rgba(245,149,32,0.35))",
  },

  selection: {
    ring: "var(--color-viz-selection)",
    ringAlpha: "var(--color-accent-muted, rgba(245,149,32,0.16))",
  },

  complexity: {
    simple: "var(--color-success)",
    moderate: "var(--color-warning)",
    complex: "var(--color-error)",
  } as Record<string, string>,

  /** Status badge glyphs (entry / hotspot / dead) — semantic tokens, both themes. */
  status: {
    entry: "var(--color-success)",
    hotspot: "var(--color-error)",
    dead: "var(--color-text-muted)",
  },

  /** Health-score buckets (≥80 / ≥60 / below). */
  health: {
    good: "var(--color-success)",
    fair: "var(--color-warning)",
    poor: "var(--color-error)",
  },

  edge: {
    imports: "var(--color-edge-imports)",
    depends_on: "var(--color-warning)",
    contains: "var(--color-accent-secondary)",
    tested_by: "var(--color-success)",
    default: "var(--color-text-tertiary)",
  } as Record<string, string>,

  diff: {
    changed: "var(--color-viz-diff-changed)",
    changedAlpha: "rgba(252,165,165,0.4)",
    affected: "var(--color-viz-diff-affected)",
    affectedAlpha: "rgba(251,191,36,0.3)",
  },

  font: {
    sans: "var(--font-sans, system-ui, sans-serif)",
    mono: "var(--font-mono, ui-monospace, monospace)",
  },

  radius: {
    sm: 4,
    md: 8,
    lg: 12,
  },

  shadow: {
    panel: "0 10px 30px rgba(0,0,0,0.35)",
    tooltip: "0 8px 24px rgba(0,0,0,0.4)",
  },
} as const;

/** Edge color for a relation type, falling back to the muted default. */
export function edgeColor(edgeType: string): string {
  return THEME.edge[edgeType] ?? "var(--color-text-tertiary)";
}

export const KEYFRAMES = {
  accentPulse: `
@keyframes accentPulse {
  0%, 100% { box-shadow: 0 0 0 0 rgba(245, 149, 32, 0.3); }
  50% { box-shadow: 0 0 12px 4px rgba(245, 149, 32, 0.15); }
}`,
  edgeFlow: `
@keyframes edgeFlow {
  to { stroke-dashoffset: -20; }
}`,
  fadeSlideIn: `
@keyframes fadeSlideIn {
  from { opacity: 0; transform: translateY(8px); }
  to { opacity: 1; transform: translateY(0); }
}`,
} as const;
