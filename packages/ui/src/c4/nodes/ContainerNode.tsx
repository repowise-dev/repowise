"use client";

import { memo, type ReactNode } from "react";
import { Files, Flame, Skull } from "lucide-react";
import type { NodeProps } from "@xyflow/react";
import { NodeShell } from "./node-shell";
import { THEME } from "../theme/theme-variables";
import { containerKindLabel } from "../../zoom/c4";
import { techName } from "../../zoom/tech-names";
import type { C4Container } from "../types";

/**
 * The band label and tone. A library is linked into the containers that run,
 * so it gets its own quieter tone; with no kind joined in, the plain
 * "Container · language" label stays.
 */
function containerIdentity(c: C4Container): { tone: string; kindLabel: string } {
  if (c.kind === "library") return { tone: "library", kindLabel: "Library" };
  if (c.kind) return { tone: "container", kindLabel: `Container · ${containerKindLabel(c.kind)}` };
  return { tone: "container", kindLabel: c.language ? `Container · ${c.language}` : "Container" };
}

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
  const { tone, kindLabel } = containerIdentity(container);
  const tech = container.technologies?.length
    ? container.technologies.map(techName).join(" · ")
    : undefined;
  return (
    <NodeShell
      tone={tone}
      kindLabel={kindLabel}
      title={container.name}
      subtitle={tech ?? (container.path !== container.name ? container.path : undefined)}
      selected={selected}
      hasDocs={hasDocs}
      footer={<div style={{ display: "flex", gap: 10 }}>{chips}</div>}
    />
  );
}

export const ContainerNode = memo(ContainerNodeImpl);
