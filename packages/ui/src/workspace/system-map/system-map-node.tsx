"use client";

/**
 * A service on the Live System Map: a surface-plane object with a hairline
 * border, named in full, with where it lives, its repository's health mark in
 * the canonical three bands, and how many contracts it provides and consumes.
 *
 * The architecture role is a word, not a colour: the health mark already owns
 * green, amber and red on this object, and a second mark in the same
 * vocabulary would read as a second health reading.
 */

import { memo } from "react";
import { Handle, Position, type NodeProps } from "@xyflow/react";
import { healthMark } from "./system-map-model";
import { SYSTEM_MAP_NODE_SIZE } from "./layout";
import type { SystemMapNodeData } from "./types";

function badgeColor(tone: "danger" | "warning" | "info"): string {
  if (tone === "danger") return "var(--color-error)";
  if (tone === "warning") return "var(--color-warning)";
  return "var(--color-text-secondary)";
}

function SystemMapNodeInner({ data, selected }: NodeProps) {
  const { node, health, overlay, role } = data as unknown as SystemMapNodeData;
  const mark = health ? healthMark(health.score) : null;
  const lensed = overlay?.highlighted ?? false;
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
      <Handle type="target" position={Position.Top} style={{ opacity: 0, pointerEvents: "none" }} />

      {overlay?.badge && (
        <span
          style={{
            position: "absolute",
            top: -9,
            right: 10,
            padding: "0 6px",
            borderRadius: 4,
            background: "var(--color-bg-surface)",
            border: `1px solid ${badgeColor(overlay.badge.tone)}`,
            color: badgeColor(overlay.badge.tone),
            fontFamily: "var(--font-mono, ui-monospace, monospace)",
            fontSize: 10,
            lineHeight: "16px",
            fontWeight: 600,
          }}
        >
          {overlay.badge.label}
        </span>
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
              color: "var(--color-text-tertiary)",
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
          color: "var(--color-text-tertiary)",
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
        {node.is_isolated
          ? "no cross-service edges"
          : `${node.provider_count.toLocaleString()} provided · ${node.consumer_count.toLocaleString()} consumed`}
      </span>

      <Handle type="source" position={Position.Bottom} style={{ opacity: 0, pointerEvents: "none" }} />
    </div>
  );
}

export const SystemMapNode = memo(SystemMapNodeInner);

export const systemMapNodeTypes = { systemService: SystemMapNode };
