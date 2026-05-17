"use client";

import { memo } from "react";
import { User } from "lucide-react";
import type { NodeProps } from "@xyflow/react";
import { NodeShell } from "./node-shell";
import type { C4Person } from "../types";

export interface PersonNodeProps {
  person: C4Person;
}

function PersonNodeImpl(props: NodeProps) {
  const { data, selected } = props as NodeProps & { data: PersonNodeProps };
  const { person } = data;
  return (
    <NodeShell
      tone="person"
      kindLabel="Person"
      title={person.name}
      subtitle={person.description || undefined}
      selected={selected}
      footer={
        <span style={{ display: "inline-flex", alignItems: "center", gap: 4 }}>
          <User size={11} aria-hidden /> actor
        </span>
      }
    />
  );
}

export const PersonNode = memo(PersonNodeImpl);
