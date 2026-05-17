"use client";

import { memo } from "react";
import { Box, Cloud, Wrench, Library } from "lucide-react";
import type { NodeProps } from "@xyflow/react";
import { NodeShell } from "./node-shell";
import type { C4ExternalSystem } from "../types";

export interface ExternalSystemNodeProps {
  external: C4ExternalSystem;
}

function categoryIcon(category: string) {
  switch (category) {
    case "service":
      return Cloud;
    case "tool":
      return Wrench;
    case "framework":
      return Box;
    default:
      return Library;
  }
}

function ExternalSystemNodeImpl(props: NodeProps) {
  const { data, selected } = props as NodeProps & { data: ExternalSystemNodeProps };
  const { external } = data;
  const Icon = categoryIcon(external.category);
  const version = external.version ? `v${external.version.replace(/^[\^~]/, "")}` : null;
  return (
    <NodeShell
      tone="external"
      kindLabel={external.category || "external"}
      title={external.display_name || external.name}
      subtitle={external.ecosystem}
      selected={selected}
      footer={
        <span style={{ display: "inline-flex", alignItems: "center", gap: 4 }}>
          <Icon size={11} aria-hidden /> {version ?? external.name}
        </span>
      }
    />
  );
}

export const ExternalSystemNode = memo(ExternalSystemNodeImpl);
