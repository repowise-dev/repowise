"use client";

/**
 * Shared visual chrome for every C4 node — a rounded rect with a tinted
 * header band, a kind label, a title and an optional subtitle/footer slot.
 *
 * Keeps each concrete node component (~30 lines) declarative.
 */

import * as React from "react";
import { BookOpen } from "lucide-react";
import { Handle, Position } from "@xyflow/react";

export type NodeTone =
  | "system"
  | "person"
  | "external"
  | "container"
  | "component";

const TONE_STYLES: Record<NodeTone, { bg: string; border: string; band: string; text: string }> = {
  system:    { bg: "#1f3a8a", border: "#1e40af", band: "#1e40af", text: "#ffffff" },
  person:    { bg: "#374151", border: "#4b5563", band: "#4b5563", text: "#ffffff" },
  external:  { bg: "#1f2937", border: "#374151", band: "#374151", text: "#e5e7eb" },
  container: { bg: "#0f3a5f", border: "#1d4ed8", band: "#1d4ed8", text: "#ffffff" },
  component: { bg: "#0d2a47", border: "#1e3a8a", band: "#1e3a8a", text: "#e5e7eb" },
};

export interface NodeShellProps {
  tone: NodeTone;
  kindLabel: string;
  title: string;
  subtitle?: string | undefined;
  footer?: React.ReactNode | undefined;
  selected?: boolean | undefined;
  width?: number | undefined;
  height?: number | undefined;
  hasDocs?: boolean | undefined;
}

export function NodeShell({
  tone,
  kindLabel,
  title,
  subtitle,
  footer,
  selected,
  width,
  height,
  hasDocs,
}: NodeShellProps) {
  const s = TONE_STYLES[tone];
  return (
    <div
      data-c4-tone={tone}
      style={{
        width,
        height,
        background: s.bg,
        border: `1.5px solid ${selected ? "#fbbf24" : s.border}`,
        borderRadius: 8,
        color: s.text,
        overflow: "hidden",
        boxShadow: selected ? "0 0 0 2px rgba(251,191,36,0.4)" : "0 1px 2px rgba(0,0,0,0.2)",
        fontFamily: "var(--font-sans, system-ui, sans-serif)",
        display: "flex",
        flexDirection: "column",
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
        {hasDocs && (
          <span
            title="Documentation available"
            aria-label="Documentation available"
            style={{ display: "inline-flex", alignItems: "center", opacity: 0.95 }}
          >
            <BookOpen size={10} aria-hidden />
          </span>
        )}
      </div>
      <div style={{ padding: "8px 10px", flex: 1, display: "flex", flexDirection: "column", gap: 2 }}>
        <div style={{ fontSize: 13, fontWeight: 600, lineHeight: 1.25, wordBreak: "break-word" }}>
          {title}
        </div>
        {subtitle && (
          <div style={{ fontSize: 11, opacity: 0.75, lineHeight: 1.3, wordBreak: "break-word" }}>
            {subtitle}
          </div>
        )}
        {footer && <div style={{ marginTop: "auto", paddingTop: 4, fontSize: 10, opacity: 0.8 }}>{footer}</div>}
      </div>
      <Handle type="source" position={Position.Bottom} style={{ opacity: 0 }} />
    </div>
  );
}
