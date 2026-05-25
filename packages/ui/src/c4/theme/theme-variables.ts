export const THEME = {
  canvas: {
    bg: "var(--color-bg-canvas, #0f172a)",
    dot: "var(--color-canvas-dot, rgba(148,163,184,0.08))",
  },

  surface: {
    elevated: "var(--color-bg-elevated, rgba(17,24,39,0.96))",
    overlay: "var(--color-bg-overlay, rgba(15,23,42,0.92))",
    glass: "var(--color-bg-glass, rgba(17,24,39,0.75))",
    glassBlur: "blur(8px)",
  },

  border: {
    default: "var(--color-border-default, #334155)",
    subtle: "var(--color-border-subtle, rgba(148,163,184,0.12))",
  },

  text: {
    primary: "var(--color-text-primary, #f1f5f9)",
    secondary: "var(--color-text-secondary, #94a3b8)",
    muted: "var(--color-text-muted, #64748b)",
  },

  accent: {
    primary: "var(--color-accent-primary, #f59520)",
    muted: "var(--color-accent-muted, rgba(245,149,32,0.2))",
    hover: "var(--color-accent-hover, rgba(245,149,32,0.35))",
  },

  selection: {
    ring: "#fbbf24",
    ringAlpha: "rgba(251,191,36,0.4)",
  },

  complexity: {
    simple: "#22c55e",
    moderate: "#f59e0b",
    complex: "#ef4444",
  } as Record<string, string>,

  edge: {
    imports: "#d4a754",
    depends_on: "#e8976b",
    contains: "#a78bfa",
    tested_by: "#6ee7b7",
    default: "#8b9dc3",
  } as Record<string, string>,

  diff: {
    changed: "#fca5a5",
    changedAlpha: "rgba(252,165,165,0.4)",
    affected: "#fbbf24",
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
