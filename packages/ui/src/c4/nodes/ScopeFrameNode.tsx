"use client";

/**
 * "You are here" scope frame (kg-ux plan §2.4) — the dashed rounded
 * boundary from the reference flowchart, drawn behind the drilled tier's
 * nodes. Non-interactive underlay: clicks pass through to the pane, the
 * name tab on the border says which scope you're inside.
 */

import { memo } from "react";
import type { NodeProps } from "@xyflow/react";

export interface ScopeFrameNodeProps {
  label: string;
  width: number;
  height: number;
}

function ScopeFrameNodeImpl(props: NodeProps) {
  const { data } = props as NodeProps & { data: ScopeFrameNodeProps };
  const { label, width, height } = data;

  return (
    <div
      aria-hidden
      style={{
        width,
        height,
        border: "1.5px dashed var(--color-diagram-cluster-border)",
        borderRadius: 16,
        pointerEvents: "none",
        position: "relative",
      }}
    >
      {/* Faint warm wash inside the boundary — garnish, sits behind cards. */}
      <div
        style={{
          position: "absolute",
          inset: 0,
          borderRadius: 16,
          backgroundImage: "var(--gradient-warm-wash)",
          opacity: 0.4,
        }}
      />
      {/* Name tab interrupting the dashed border */}
      <span
        style={{
          position: "absolute",
          top: -9,
          left: 20,
          padding: "1px 10px",
          background: "var(--color-bg-canvas)",
          border: "1px dashed var(--color-diagram-cluster-border)",
          borderRadius: 6,
          fontFamily: "var(--font-mono, ui-monospace, monospace)",
          fontSize: 9,
          fontWeight: 600,
          letterSpacing: 1,
          textTransform: "uppercase",
          color: "var(--color-text-secondary)",
          whiteSpace: "nowrap",
        }}
      >
        {label}
      </span>
    </div>
  );
}

export const ScopeFrameNode = memo(ScopeFrameNodeImpl);
