"use client";

import * as React from "react";
import { BookOpen } from "lucide-react";
import { Handle, Position } from "@xyflow/react";
import { getTone } from "./tone-styles";

export interface NodeShellProps {
  tone: string;
  kindLabel: string;
  title: string;
  subtitle?: string | undefined;
  footer?: React.ReactNode | undefined;
  selected?: boolean | undefined;
  focused?: boolean | undefined;
  searchHighlight?: boolean | undefined;
  tourHighlight?: boolean | undefined;
  diffState?: "changed" | "affected" | "faded" | undefined;
  width?: number | undefined;
  height?: number | undefined;
  hasDocs?: boolean | undefined;
  badges?: React.ReactNode | undefined;
  titleFontSize?: number | undefined;
  subtitleFontSize?: number | undefined;
  subtitleLineClamp?: number | undefined;
}

function getBorderColor(props: NodeShellProps, fallbackBorder: string): string {
  if (props.selected) return "var(--color-viz-selection)";
  if (props.diffState === "changed") return "var(--color-viz-diff-changed)";
  if (props.diffState === "affected") return "var(--color-viz-diff-affected)";
  if (props.tourHighlight) return "var(--color-accent-fill)";
  if (props.searchHighlight) return "var(--color-accent-fill)";
  if (props.focused) return "var(--color-accent-fill)";
  return fallbackBorder;
}

function getBoxShadow(props: NodeShellProps): string {
  if (props.selected) return "0 0 0 2px color-mix(in srgb, var(--color-viz-selection) 40%, transparent)";
  if (props.diffState === "changed") return "0 0 0 2px color-mix(in srgb, var(--color-viz-diff-changed) 40%, transparent)";
  if (props.diffState === "affected") return "0 0 0 2px color-mix(in srgb, var(--color-viz-diff-affected) 35%, transparent)";
  return "0 1px 2px rgba(0,0,0,0.2)";
}

function getBorderStyle(props: NodeShellProps): string {
  if (props.searchHighlight && !props.selected) return "dashed";
  return "solid";
}

export function NodeShell(props: NodeShellProps) {
  const {
    tone,
    kindLabel,
    title,
    subtitle,
    footer,
    selected,
    tourHighlight,
    diffState,
    width,
    height,
    hasDocs,
    badges,
    titleFontSize = 14,
    subtitleFontSize = 11,
    subtitleLineClamp = 3,
  } = props;

  const s = getTone(tone);
  const opacity = diffState === "faded" ? 0.25 : 1;

  return (
    <div
      data-c4-tone={tone}
      style={{
        width,
        height,
        background: s.bg,
        border: `1.5px ${getBorderStyle(props)} ${getBorderColor(props, s.border)}`,
        borderLeft: `3px solid ${selected ? "var(--color-viz-selection)" : s.band}`,
        borderRadius: 8,
        color: s.text,
        overflow: "hidden",
        boxShadow: getBoxShadow(props),
        fontFamily: "var(--font-sans, system-ui, sans-serif)",
        display: "flex",
        flexDirection: "column",
        opacity,
        animation: tourHighlight && !selected ? "accentPulse 2s ease-in-out infinite" : undefined,
      }}
    >
      <Handle type="target" position={Position.Top} style={{ opacity: 0 }} />
      <div
        style={{
          background: s.band,
          padding: "3px 8px",
          fontSize: 9,
          fontWeight: 600,
          letterSpacing: 0.6,
          textTransform: "uppercase",
          opacity: 0.85,
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          gap: 6,
        }}
      >
        <span>{kindLabel}</span>
        <span style={{ display: "inline-flex", alignItems: "center", gap: 4 }}>
          {badges}
          {hasDocs && (
            <span
              title="Documentation available"
              aria-label="Documentation available"
              style={{ display: "inline-flex", alignItems: "center", opacity: 0.95 }}
            >
              <BookOpen size={10} aria-hidden />
            </span>
          )}
        </span>
      </div>
      <div style={{ padding: "8px 10px", flex: 1, display: "flex", flexDirection: "column", gap: 2 }}>
        <div style={{ fontSize: titleFontSize, fontWeight: 600, lineHeight: 1.25, wordBreak: "break-word" }}>
          {title}
        </div>
        {subtitle && (
          <div
            style={{
              fontSize: subtitleFontSize,
              opacity: 0.75,
              lineHeight: 1.3,
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
        {footer && <div style={{ marginTop: "auto", paddingTop: 4, fontSize: 10, opacity: 0.8 }}>{footer}</div>}
      </div>
      <Handle type="source" position={Position.Bottom} style={{ opacity: 0 }} />
    </div>
  );
}
