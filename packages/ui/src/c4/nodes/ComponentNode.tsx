"use client";

import { memo } from "react";
import { Files } from "lucide-react";
import type { NodeProps } from "@xyflow/react";
import { NodeShell } from "./node-shell";
import type { C4Component } from "../types";

export interface ComponentNodeProps {
  component: C4Component;
  hasDocs?: boolean;
}

function ComponentNodeImpl(props: NodeProps) {
  const { data, selected } = props as NodeProps & { data: ComponentNodeProps };
  const { component, hasDocs } = data;
  return (
    <NodeShell
      tone="component"
      kindLabel="Component"
      title={component.name === "_root" ? "(root)" : component.name}
      subtitle={component.path !== component.name ? component.path : undefined}
      selected={selected}
      hasDocs={hasDocs}
      footer={
        <span style={{ display: "inline-flex", alignItems: "center", gap: 3 }}>
          <Files size={11} aria-hidden /> {component.file_count} files · {component.symbol_count} symbols
        </span>
      }
    />
  );
}

export const ComponentNode = memo(ComponentNodeImpl);
