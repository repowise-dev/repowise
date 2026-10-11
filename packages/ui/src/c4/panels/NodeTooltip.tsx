"use client";

import type { ArchNode } from "../types";
import { getTone } from "../../graph-primitives/tone-styles";
import { THEME } from "../theme/theme-variables";
import { Badge } from "./panel-atoms";

export interface NodeTooltipProps {
  node: ArchNode | null;
  position: { x: number; y: number } | null;
}

const MAX_SUMMARY_LEN = 120;
const MAX_VISIBLE_TAGS = 3;

export function NodeTooltip({ node, position }: NodeTooltipProps) {
  if (!node || !position) return null;

  const tone = getTone(node.node_type);
  const dotColor = THEME.complexity[node.complexity] ?? THEME.text.muted;

  const truncatedSummary =
    node.summary.length > MAX_SUMMARY_LEN
      ? node.summary.slice(0, MAX_SUMMARY_LEN) + "…"
      : node.summary;

  const visibleTags = node.tags.slice(0, MAX_VISIBLE_TAGS);
  const remainingTags = node.tags.length - MAX_VISIBLE_TAGS;

  return (
    <div
      role="tooltip"
      style={{
        position: "absolute",
        left: position.x + 16,
        top: position.y + 16,
        zIndex: 50,
        maxWidth: 280,
        padding: "10px 12px",
        background: "var(--color-bg-glass, rgba(17,24,39,0.75))",
        backdropFilter: "blur(8px)",
        WebkitBackdropFilter: "blur(8px)",
        border: "1px solid var(--color-border-default)",
        borderRadius: 8,
        color: "var(--color-text-primary)",
        fontSize: 11,
        fontFamily: "var(--font-sans, system-ui, sans-serif)",
        boxShadow: "0 8px 24px rgba(0,0,0,0.4)",
        pointerEvents: "none",
        animation: "fadeSlideIn 0.15s ease-out",
      }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: 6, marginBottom: 6 }}>
        <Badge label={node.node_type} color={tone.text} bg={tone.band} />
        <span
          aria-label={`Complexity: ${node.complexity}`}
          style={{
            display: "inline-block",
            width: 8,
            height: 8,
            borderRadius: "50%",
            background: dotColor,
            flexShrink: 0,
          }}
        />
        <span
          style={{
            fontWeight: 600,
            fontSize: 12,
            overflow: "hidden",
            textOverflow: "ellipsis",
            whiteSpace: "nowrap",
          }}
        >
          {node.name}
        </span>
      </div>

      <div style={{ display: "flex", gap: 12, fontSize: 10, opacity: 0.7, marginBottom: 4 }}>
        <span>{"↓"}{node.in_degree} incoming</span>
        <span>{"↑"}{node.out_degree} outgoing</span>
      </div>

      {truncatedSummary && (
        <div
          style={{
            opacity: 0.85,
            lineHeight: 1.4,
            marginBottom: node.tags.length > 0 ? 6 : 0,
          }}
        >
          {truncatedSummary}
        </div>
      )}

      {visibleTags.length > 0 && (
        <div style={{ display: "flex", flexWrap: "wrap", gap: 4 }}>
          {visibleTags.map((tag) => (
            <span
              key={tag}
              style={{
                display: "inline-block",
                padding: "2px 8px",
                borderRadius: 10,
                fontSize: 10,
                fontWeight: 500,
                background: "var(--color-bg-wash-hover)",
                color: "var(--color-text-secondary)",
                border: "1px solid var(--color-border-default)",
              }}
            >
              {tag}
            </span>
          ))}
          {remainingTags > 0 && (
            <span
              style={{
                fontSize: 10,
                opacity: 0.6,
                alignSelf: "center",
              }}
            >
              +{remainingTags} more
            </span>
          )}
        </div>
      )}
    </div>
  );
}
