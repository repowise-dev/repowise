"use client";

import { memo, useState } from "react";
import { ChevronRight, ChevronDown } from "lucide-react";
import type { NodeProps } from "@xyflow/react";
import { Handle, Position } from "@xyflow/react";
import { useArchitectureStore } from "../store/use-architecture-store";
import { THEME } from "../theme/theme-variables";

export interface ArchContainerNodeProps {
  containerId: string;
  label: string;
  childCount: number;
  expanded: boolean;
  searchHitCount?: number | undefined;
  /** Unrelated to the current selection — fade, never vanish (plan D). */
  dimmed?: boolean | undefined;
}

function ArchContainerNodeImpl(props: NodeProps) {
  const { data, selected } = props as NodeProps & { data: ArchContainerNodeProps };
  const { containerId, label, childCount, expanded, searchHitCount, dimmed } = data;
  const [hovered, setHovered] = useState(false);

  const ChevronIcon = expanded ? ChevronDown : ChevronRight;

  // Unified click grammar (kg-ux plan B5): single click selects (page-level
  // handler); double-click expands/collapses (page-level too). Keyboard
  // Enter/Space mirrors single-click selection.
  const handleSelect = () => {
    useArchitectureStore.getState().selectNode(containerId);
  };

  // Diagram ink, not panel chrome: the panel border tokens sit at ~12% alpha
  // and disappear into the paper canvas in light mode, leaving the (full-ink)
  // edges pointing at boxes the eye can't see. Folder containers are
  // supporting-cast boxes, so they take the faded-ink outline.
  const borderColor = selected
    ? THEME.selection.ring
    : "var(--color-kg-node-border-2)";

  return (
    <div
      role="button"
      tabIndex={0}
      aria-label={`Inspect folder ${label}`}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          handleSelect();
        }
      }}
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
      style={{
        cursor: "pointer",
        width: expanded ? 320 : 260,
        minHeight: expanded ? undefined : 56,
        background: hovered ? THEME.surface.washHover : THEME.surface.wash,
        border: `1.5px solid ${borderColor}`,
        borderLeft: `3px solid ${selected ? THEME.selection.ring : "var(--color-kg-node-border-2)"}`,
        borderRadius: 12,
        padding: "10px 14px",
        display: "flex",
        flexDirection: "column",
        gap: 2,
        boxShadow: selected ? `0 0 0 2px ${THEME.selection.ringAlpha}` : "none",
        opacity: dimmed && !hovered ? 0.45 : 1,
        transition: "background 0.15s ease, opacity 0.2s ease",
      }}
    >
      <Handle type="target" position={Position.Top} style={{ opacity: 0 }} />
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
        <span style={{
          fontSize: 13,
          fontWeight: 600,
          color: "var(--color-text-primary)",
          overflow: "hidden",
          textOverflow: "ellipsis",
          whiteSpace: "nowrap",
        }}>
          {label}
        </span>
        <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
          <span style={{ fontSize: 11, color: "var(--color-text-secondary)" }}>
            {childCount}
          </span>
          <ChevronIcon size={12} color="var(--color-text-secondary)" aria-hidden />
        </div>
      </div>
      {searchHitCount != null && searchHitCount > 0 && (
        <span style={{
          fontSize: 10,
          fontFamily: "var(--font-mono, ui-monospace, monospace)",
          color: THEME.accent.primary,
          background: THEME.accent.muted,
          padding: "1px 6px",
          borderRadius: 4,
          alignSelf: "flex-start",
        }}>
          {searchHitCount} matches
        </span>
      )}
      <Handle type="source" position={Position.Bottom} style={{ opacity: 0 }} />
    </div>
  );
}

export const ArchContainerNode = memo(ArchContainerNodeImpl);
