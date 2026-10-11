"use client";

import { memo, type ComponentType } from "react";
import { Clock, Globe, Terminal, TerminalSquare, User } from "lucide-react";
import type { LucideProps } from "lucide-react";
import type { NodeProps } from "@xyflow/react";
import { NodeShell } from "./node-shell";
import type { C4Person } from "../types";

export interface PersonNodeProps {
  person: C4Person;
}

// Actor kind -> (icon, short footer label). Keeps the L1 actor visually
// distinct (a CLI user reads differently from an API client or a scheduler)
// instead of every actor being a generic person.
interface ActorPresentation {
  Icon: ComponentType<LucideProps>;
  label: string;
}

const GENERIC_ACTOR: ActorPresentation = { Icon: User, label: "actor" };

const ACTOR_PRESENTATION: Record<string, ActorPresentation> = {
  cli: { Icon: Terminal, label: "cli user" },
  api: { Icon: Globe, label: "api client" },
  scheduler: { Icon: Clock, label: "scheduled" },
  developer: { Icon: TerminalSquare, label: "developer" },
  user: GENERIC_ACTOR,
};

function PersonNodeImpl(props: NodeProps) {
  const { data, selected } = props as NodeProps & { data: PersonNodeProps };
  const { person } = data;
  const { Icon, label } = ACTOR_PRESENTATION[person.kind] ?? GENERIC_ACTOR;
  return (
    <NodeShell
      tone="person"
      kindLabel="Person"
      title={person.name}
      subtitle={person.description || undefined}
      selected={selected}
      footer={
        <span style={{ display: "inline-flex", alignItems: "center", gap: 4 }}>
          <Icon size={11} aria-hidden /> {label}
        </span>
      }
    />
  );
}

export const PersonNode = memo(PersonNodeImpl);
