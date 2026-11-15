"use client";

/**
 * A group of services on the map. Closed, it is a card: the group's name and
 * where it came from, how many services and repositories it holds, its
 * health exceptions on the canonical five bands (Good and better are quiet), and its
 * links in, out and inside. Open, it is the container its service cards sit
 * in, with a title row and a way to close it.
 *
 * No hue per group: containment and the name identify it, and the health
 * mark keeps the only colour on the card.
 */

import { memo, type CSSProperties } from "react";
import { Handle, Position, type NodeProps } from "@xyflow/react";
import { healthExceptions, healthSummaryText } from "./aggregate-facts";
import type { GroupNodeData } from "./flow-elements";
import { LEVEL_NODE_SIZES } from "./layout-scopes";
import { useSystemMapActions } from "./system-map-actions";
import { CardAction, HIDDEN_HANDLE } from "./system-map-card-action";
import { plural } from "./system-map-model";
import { formatNumber } from "../../lib/format";

const MONO: CSSProperties = {
  fontFamily: "var(--font-mono, ui-monospace, monospace)",
  fontSize: 10,
  lineHeight: "14px",
  fontVariantNumeric: "tabular-nums",
  color: "var(--color-text-secondary)",
};

const TITLE: CSSProperties = {
  minWidth: 0,
  overflow: "hidden",
  textOverflow: "ellipsis",
  whiteSpace: "nowrap",
  fontSize: 15,
  lineHeight: "20px",
  fontWeight: 600,
  color: "var(--color-text-primary)",
};

function frame(selected: boolean, highlighted: boolean): CSSProperties {
  return {
    border: `${highlighted && !selected ? 1.5 : 1}px solid ${
      selected ? "var(--color-accent-primary)" : highlighted ? "var(--color-text-primary)" : "var(--color-border-hover)"
    }`,
    boxShadow: selected ? "0 0 0 2px color-mix(in srgb, var(--color-accent-primary) 35%, transparent)" : undefined,
  };
}

function GroupNodeInner({ data, selected }: NodeProps) {
  const { group, sourceLabel, health, overlay } = data as unknown as GroupNodeData;
  const actions = useSystemMapActions();
  const services = plural(group.serviceIds.length, "service", "services");
  const links = [
    `in ${formatNumber(group.linksIn)}`,
    `out ${formatNumber(group.linksOut)}`,
    ...(group.linksInside > 0 ? [`${formatNumber(group.linksInside)} inside`] : []),
  ].join(" · ");
  const lensed = overlay?.highlighted ?? false;
  const dimmed = overlay?.dimmed ?? false;

  if (group.open) {
    return (
      <div
        className="sm-group"
        style={{
          width: "100%",
          height: "100%",
          boxSizing: "border-box",
          borderRadius: 10,
          background: "color-mix(in srgb, var(--color-bg-surface) 55%, transparent)",
          padding: "10px 14px",
          opacity: dimmed ? 0.35 : 1,
          ...frame(Boolean(selected), lensed),
        }}
      >
        <Handle type="target" position={Position.Top} style={HIDDEN_HANDLE} />
        <div style={{ display: "flex", alignItems: "baseline", gap: 10, minWidth: 0 }}>
          <span style={TITLE}>{group.label}</span>
          {sourceLabel && <span style={{ ...MONO, flexShrink: 0 }}>{sourceLabel}</span>}
          <span style={{ ...MONO, flexShrink: 0 }}>{services}</span>
          <span style={{ flex: 1 }} />
          <CardAction label="Close" onClick={() => actions.closeContainer(group.groupId)} />
        </div>
        <Handle type="source" position={Position.Bottom} style={HIDDEN_HANDLE} />
      </div>
    );
  }

  const exceptions = healthExceptions(health);
  const { width, height } = LEVEL_NODE_SIZES.group;
  return (
    <div
      className="sm-group"
      style={{
        position: "relative",
        width,
        height,
        boxSizing: "border-box",
        display: "flex",
        flexDirection: "column",
        gap: 3,
        padding: "9px 12px",
        borderRadius: 8,
        background: "var(--color-bg-surface)",
        boxShadow: "var(--shadow-sm)",
        opacity: dimmed ? 0.35 : 1,
        cursor: "pointer",
        transition: "opacity 120ms, border-color 120ms",
        ...frame(Boolean(selected), lensed),
      }}
    >
      <Handle type="target" position={Position.Top} style={HIDDEN_HANDLE} />
      <div style={{ display: "flex", alignItems: "baseline", justifyContent: "space-between", gap: 8, minWidth: 0 }}>
        <span title={group.label} style={TITLE}>
          {group.label}
        </span>
        {sourceLabel && (
          <span title={sourceLabel} style={{ ...MONO, flexShrink: 1, minWidth: 0, maxWidth: "50%", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
            {sourceLabel}
          </span>
        )}
      </div>
      <span style={MONO}>
        {services} · {plural(group.repoCount, "repo", "repos")}
      </span>
      <div style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 12, lineHeight: "16px", minWidth: 0 }}>
        {exceptions.length > 0 ? (
          exceptions.map((e) => (
            <span key={e.band} style={{ display: "inline-flex", alignItems: "center", gap: 5 }}>
              <span aria-hidden style={{ width: 6, height: 6, borderRadius: 999, background: e.color, flexShrink: 0 }} />
              <span style={{ fontVariantNumeric: "tabular-nums", color: "var(--color-text-primary)", fontWeight: 600 }}>{e.count}</span>
              <span style={{ color: "var(--color-text-secondary)" }}>{e.label}</span>
            </span>
          ))
        ) : (
          <span style={{ color: "var(--color-text-secondary)" }}>{healthSummaryText(health)}</span>
        )}
      </div>
      <div style={{ display: "flex", alignItems: "baseline", justifyContent: "space-between", gap: 8, marginTop: "auto" }}>
        <span style={MONO}>{links}</span>
        <CardAction label="Open ›" onClick={() => actions.openContainer(group.groupId)} />
      </div>
      <Handle type="source" position={Position.Bottom} style={HIDDEN_HANDLE} />
    </div>
  );
}

export const SystemMapGroupNode = memo(GroupNodeInner);
