"use client";

import { memo } from "react";
import type { NodeProps } from "@xyflow/react";
import { NodeShell } from "./node-shell";
import type { C4System } from "../types";

export interface SystemNodeProps {
  system: C4System;
}

function SystemNodeImpl(props: NodeProps) {
  const { data, selected } = props as NodeProps & { data: SystemNodeProps };
  const { system } = data;
  return (
    <NodeShell
      tone="system"
      kindLabel="System"
      title={system.name}
      subtitle={system.description || "System under analysis"}
      selected={selected}
    />
  );
}

export const SystemNode = memo(SystemNodeImpl);
