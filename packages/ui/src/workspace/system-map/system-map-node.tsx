"use client";

/**
 * A service on the Live System Map: a surface-plane object with a hairline
 * border, named in full, with where it lives, its repository's health mark in
 * the canonical three bands, and how many contracts it provides and consumes.
 *
 * The architecture role is a word, not a colour: the health mark already owns
 * green, amber and red on this object, and a second mark in the same
 * vocabulary would read as a second health reading. Lens marks are ink
 * badges for the same reason (`system-map-lens-badge.tsx`).
 */

import { memo } from "react";
import { Handle, Position, type NodeProps } from "@xyflow/react";
import { healthMark } from "../../health/tokens";
import { SYSTEM_MAP_NODE_SIZE } from "./layout-scopes";
import { SystemMapLensBadge } from "./system-map-lens-badge";
import type { SystemMapNodeData } from "./types";

function SystemMapNodeInner({ data, selected }: NodeProps) {
  const { node, health, overlay, role, horizontal, focusLinks, endpoint } = data as unknown as SystemMapNodeData;
  // Repo health arrives 0-100; the mark reads 0-10.
  const mark = health ? healthMark(health.score / 10) : null;
  // A compared service is marked like a lensed one: ink, never a hue.
  const lensed = (overlay?.highlighted ?? false) || Boolean(endpoint);
  const dimmed = overlay?.dimmed ?? false;
  const where = node.service_path ? `${node.repo} / ${node.service_path}` : `${node.repo} repository`;

  const border = selected
    ? "var(--color-accent-primary)"
    : lensed
      ? "var(--color-text-primary)"
      : "var(--color-border-hover)";

  return (
    <div
      className="sm-node"
      style={{
        position: "relative",
        width: SYSTEM_MAP_NODE_SIZE.width,
        height: SYSTEM_MAP_NODE_SIZE.height,
        boxSizing: "border-box",
        display: "flex",
        flexDirection: "column",
        gap: 3,
        padding: "9px 12px",
        borderRadius: 8,
        background: "var(--color-bg-surface)",
        border: `1px solid ${border}`,
        boxShadow: selected ? "0 0 0 2px color-mix(in srgb, var(--color-accent-primary) 35%, transparent)" : "var(--shadow-sm)",
        opacity: dimmed ? 0.35 : 1,
        cursor: "pointer",
        transition: "opacity 120ms, border-color 120ms",
      }}
    >
      <Handle type="target" position={horizontal ? Position.Left : Position.Top} style={{ opacity: 0, pointerEvents: "none" }} />

      {overlay?.badge && (
        <SystemMapLensBadge badge={overlay.badge} style={{ position: "absolute", top: -9, right: 10 }} />
      )}

      <div style={{ display: "flex", alignItems: "baseline", justifyContent: "space-between", gap: 8, minWidth: 0 }}>
        <span
          title={node.name}
          style={{
            minWidth: 0,
            overflow: "hidden",
            textOverflow: "ellipsis",
            whiteSpace: "nowrap",
            fontSize: 15,
            lineHeight: "20px",
            fontWeight: 600,
            color: "var(--color-text-primary)",
          }}
        >
          {node.name}
        </span>
        {role && (
          <span
            style={{
              flexShrink: 0,
              fontFamily: "var(--font-mono, ui-monospace, monospace)",
              fontSize: 10,
              letterSpacing: "0.08em",
              textTransform: "uppercase",
              color: "var(--color-text-secondary)",
            }}
          >
            {role}
          </span>
        )}
      </div>

      <span
        title={where}
        style={{
          overflow: "hidden",
          textOverflow: "ellipsis",
          whiteSpace: "nowrap",
          fontFamily: "var(--font-mono, ui-monospace, monospace)",
          fontSize: 10,
          lineHeight: "14px",
          color: "var(--color-text-secondary)",
        }}
      >
        {where}
      </span>

      <div style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 12, lineHeight: "16px" }}>
        {mark ? (
          <>
            <span aria-hidden style={{ width: 6, height: 6, borderRadius: 999, background: mark.color, flexShrink: 0 }} />
            <span style={{ fontVariantNumeric: "tabular-nums", color: "var(--color-text-primary)", fontWeight: 600 }}>
              {mark.value}
            </span>
            <span style={{ color: "var(--color-text-secondary)" }}>{mark.label}</span>
          </>
        ) : (
          <span style={{ color: "var(--color-text-tertiary)" }}>No health score</span>
        )}
      </div>

      <span
        style={{
          fontFamily: "var(--font-mono, ui-monospace, monospace)",
          fontSize: 10,
          lineHeight: "14px",
          fontVariantNumeric: "tabular-nums",
          color: "var(--color-text-secondary)",
        }}
      >
        {focusLinks ??
          (node.is_isolated
            ? "no cross-service edges"
            : `${node.provider_count.toLocaleString()} provided · ${node.consumer_count.toLocaleString()} consumed`)}
      </span>

      <Handle type="source" position={horizontal ? Position.Right : Position.Bottom} style={{ opacity: 0, pointerEvents: "none" }} />
    </div>
  );
}

export const SystemMapNode = memo(SystemMapNodeInner);

export const systemMapNodeTypes = { systemService: SystemMapNode };
