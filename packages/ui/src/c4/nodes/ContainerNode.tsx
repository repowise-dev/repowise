"use client";

import { memo, type ReactNode } from "react";
import { Files, Flame, Skull } from "lucide-react";
import type { NodeProps } from "@xyflow/react";
import { NodeShell } from "./node-shell";
import { THEME } from "../theme/theme-variables";
import type { C4Container } from "../types";

export interface ContainerNodeProps {
  container: C4Container;
  hasDocs?: boolean;
}

function ContainerNodeImpl(props: NodeProps) {
  const { data, selected } = props as NodeProps & { data: ContainerNodeProps };
  const { container, hasDocs } = data;
  const chips: ReactNode[] = [
    <span key="files" style={{ display: "inline-flex", alignItems: "center", gap: 3 }}>
      <Files size={11} aria-hidden /> {container.file_count}
    </span>,
  ];
  if (container.hotspot_count > 0) {
    chips.push(
      <span key="hot" style={{ display: "inline-flex", alignItems: "center", gap: 3, color: THEME.status.hotspot }}>
        <Flame size={11} aria-hidden /> {container.hotspot_count}
      </span>,
    );
  }
  if (container.dead_count > 0) {
    chips.push(
      <span key="dead" style={{ display: "inline-flex", alignItems: "center", gap: 3, color: THEME.status.dead }}>
        <Skull size={11} aria-hidden /> {container.dead_count}
      </span>,
    );
  }
  return (
    <NodeShell
      tone="container"
      kindLabel={container.language ? `Container · ${container.language}` : "Container"}
      title={container.name}
      subtitle={container.path !== container.name ? container.path : undefined}
      selected={selected}
      hasDocs={hasDocs}
      footer={<div style={{ display: "flex", gap: 10 }}>{chips}</div>}
    />
  );
}

export const ContainerNode = memo(ContainerNodeImpl);
