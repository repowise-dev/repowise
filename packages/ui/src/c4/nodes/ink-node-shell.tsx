"use client";

/**
 * Blueprint ink node face (kg-ux plan §2.2) — the KG canvas card.
 *
 * Color encodes ROLE, not type (the kind icon carries type):
 *   accent    — ember-gradient "start here" block (entry points, repo core,
 *               current tour step); dark text per locked decision 1.
 *   primary   — ink block: the nodes of the active scope.
 *   secondary — recessed gray-plum ink: supporting cast (collapsed siblings,
 *               barrels, demoted Test layer, "+N more").
 *   ghost     — transparent face, dashed boundary (placeholders, portals).
 */

import * as React from "react";
import { BookOpen, type LucideIcon } from "lucide-react";
import { Handle, Position } from "@xyflow/react";

export type InkRole = "accent" | "primary" | "secondary" | "ghost";

export interface InkNodeShellProps {
  role: InkRole;
  kindLabel: string;
  title: string;
  /** Kind glyph (from getKindIcon) — renders beside the title, and as a
   * large faint watermark when `heroIcon` is set (layer/group cards). */
  icon?: LucideIcon | undefined;
  subtitle?: string | undefined;
  /** Bottom-right meta (degrees, complexity bars, counts). */
  meta?: React.ReactNode | undefined;
  /** Top-right signal glyphs (tour step, entry, hotspot, dead). */
  badges?: React.ReactNode | undefined;
  selected?: boolean | undefined;
  focused?: boolean | undefined;
  searchHighlight?: boolean | undefined;
  tourHighlight?: boolean | undefined;
  diffState?: "changed" | "affected" | "faded" | undefined;
  width?: number | undefined;
  height?: number | undefined;
  hasDocs?: boolean | undefined;
  titleFontSize?: number | undefined;
  subtitleLineClamp?: number | undefined;
  heroIcon?: boolean | undefined;
}

const FACES: Record<InkRole, { background: string; backgroundImage?: string; text: string; border: string }> = {
  accent: {
    background: "var(--color-accent-fill)",
    backgroundImage: "var(--gradient-ember)",
    text: "var(--color-text-on-accent)",
    border: "transparent",
  },
  primary: {
    background: "var(--color-kg-node-fill)",
    // Ruled-paper texture in light mode (token resolves to none in dark).
    backgroundImage: "var(--kg-card-texture)",
    text: "var(--color-kg-node-text)",
    border: "var(--color-kg-node-border)",
  },
  secondary: {
    background: "var(--color-kg-node-fill-2)",
    text: "var(--color-kg-node-text)",
    border: "var(--color-kg-node-border-2)",
  },
  ghost: {
    background: "transparent",
    text: "var(--color-text-primary)",
    border: "var(--color-diagram-cluster-border)",
  },
};

function stateBorderColor(props: InkNodeShellProps, faceBorder: string): string {
  if (props.selected) return "var(--color-viz-selection)";
  if (props.diffState === "changed") return "var(--color-viz-diff-changed)";
  if (props.diffState === "affected") return "var(--color-viz-diff-affected)";
  if (props.tourHighlight) return "var(--color-accent-fill)";
  if (props.searchHighlight) return "var(--color-accent-fill)";
  if (props.focused) return "var(--color-accent-fill)";
  return faceBorder;
}

function stateBoxShadow(props: InkNodeShellProps): string {
  if (props.selected) {
    return "0 0 0 2px color-mix(in srgb, var(--color-viz-selection) 35%, transparent)";
  }
  if (props.diffState === "changed") {
    return "0 0 0 2px color-mix(in srgb, var(--color-viz-diff-changed) 40%, transparent)";
  }
  if (props.diffState === "affected") {
    return "0 0 0 2px color-mix(in srgb, var(--color-viz-diff-affected) 35%, transparent)";
  }
  return "0 1px 2px rgba(0,0,0,0.15)";
}

export function InkNodeShell(props: InkNodeShellProps) {
  const {
    role,
    kindLabel,
    title,
    icon: Icon,
    subtitle,
    meta,
    badges,
    selected,
    searchHighlight,
    tourHighlight,
    diffState,
    width,
    height,
    hasDocs,
    titleFontSize = 12,
    subtitleLineClamp = 2,
    heroIcon,
  } = props;

  const face = FACES[role];
  const opacity = diffState === "faded" ? 0.25 : 1;
  const dashed = role === "ghost" || (searchHighlight && !selected);

  return (
    <div
      data-ink-role={role}
      style={{
        width,
        height,
        background: face.background,
        ...(face.backgroundImage
          ? { backgroundImage: face.backgroundImage, backgroundSize: "cover", backgroundPosition: "center" }
          : {}),
        border: `1.5px ${dashed ? "dashed" : "solid"} ${stateBorderColor(props, face.border)}`,
        borderRadius: 12,
        color: face.text,
        overflow: "hidden",
        boxShadow: stateBoxShadow(props),
        fontFamily: "var(--font-sans, system-ui, sans-serif)",
        display: "flex",
        flexDirection: "column",
        padding: "10px 12px",
        opacity,
        animation: tourHighlight && !selected ? "accentPulse 2s ease-in-out infinite" : undefined,
      }}
    >
      <Handle type="target" position={Position.Top} style={{ opacity: 0 }} />

      {/* Title row: kind glyph + mono name + signal badges */}
      <div style={{ display: "flex", alignItems: "flex-start", gap: 6 }}>
        {Icon && !heroIcon && (
          <Icon size={14} strokeWidth={2} aria-hidden style={{ flexShrink: 0, marginTop: 1, opacity: 0.9 }} />
        )}
        <div
          style={{
            flex: 1,
            minWidth: 0,
            fontFamily: "var(--font-mono, ui-monospace, monospace)",
            fontSize: titleFontSize,
            fontWeight: 600,
            lineHeight: 1.3,
            wordBreak: "break-word",
          }}
        >
          {title}
        </div>
        <span style={{ display: "inline-flex", alignItems: "center", gap: 4, flexShrink: 0 }}>
          {badges}
          {hasDocs && (
            <span
              title="Documentation available"
              aria-label="Documentation available"
              style={{ display: "inline-flex", alignItems: "center", opacity: 0.9 }}
            >
              <BookOpen size={11} aria-hidden />
            </span>
          )}
        </span>
      </div>

      {subtitle && (
        <div
          style={{
            marginTop: 3,
            fontSize: 10.5,
            opacity: 0.72,
            lineHeight: 1.35,
            wordBreak: "break-word",
            display: "-webkit-box",
            WebkitLineClamp: subtitleLineClamp,
            WebkitBoxOrient: "vertical",
            overflow: "hidden",
          }}
        >
          {subtitle}
        </div>
      )}

      {/* Hero watermark glyph — layer/group cards have the room for it */}
      {heroIcon && Icon && (
        <div style={{ flex: 1, display: "flex", alignItems: "center", justifyContent: "center", minHeight: 0 }}>
          <Icon size={36} strokeWidth={1.25} aria-hidden style={{ opacity: 0.2 }} />
        </div>
      )}

      {/* Footer: kind label left, meta right */}
      <div
        style={{
          marginTop: "auto",
          paddingTop: 6,
          display: "flex",
          alignItems: "flex-end",
          justifyContent: "space-between",
          gap: 8,
        }}
      >
        <span
          style={{
            fontFamily: "var(--font-mono, ui-monospace, monospace)",
            fontSize: 8.5,
            fontWeight: 600,
            letterSpacing: 0.8,
            textTransform: "uppercase",
            opacity: 0.6,
            whiteSpace: "nowrap",
          }}
        >
          {kindLabel}
        </span>
        {meta && <span style={{ fontSize: 10, opacity: 0.85 }}>{meta}</span>}
      </div>

      <Handle type="source" position={Position.Bottom} style={{ opacity: 0 }} />
    </div>
  );
}
