"use client";

import { memo } from "react";
import {
  Box,
  Cloud,
  Database,
  FolderOpen,
  Library,
  Lock,
  Terminal,
  Wrench,
} from "lucide-react";
import type { NodeProps } from "@xyflow/react";
import { NodeShell } from "./node-shell";
import type { C4ExternalSystem } from "../types";

export interface ExternalSystemNodeProps {
  external: C4ExternalSystem;
}

/** Human label for the typed boundary, shown in place of the coarse category. */
const IO_KIND_LABEL: Record<string, string> = {
  db: "Database",
  network: "External API",
  filesystem: "Filesystem",
  subprocess: "Subprocess",
  lock: "Lock",
};

function ioKindIcon(ioKind: string) {
  switch (ioKind) {
    case "db":
      return Database;
    case "network":
      return Cloud;
    case "filesystem":
      return FolderOpen;
    case "subprocess":
      return Terminal;
    case "lock":
      return Lock;
    default:
      return Library;
  }
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
  // Prefer the typed boundary; fall back to the coarse category when untyped.
  const ioKind = external.io_kind ?? null;
  const Icon = ioKind ? ioKindIcon(ioKind) : categoryIcon(external.category);
  const kindLabel = (ioKind && IO_KIND_LABEL[ioKind]) || external.category || "external";
  const version = external.version ? `v${external.version.replace(/^[\^~]/, "")}` : null;
  return (
    <NodeShell
      tone="external"
      kindLabel={kindLabel}
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
