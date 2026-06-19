/**
 * Node-kind registry — maps each `SystemNode.kind` to a categorical tone (from
 * the shared graph-primitives palette) and a display label. The single source
 * of truth for how a service node looks; adding a kind is one entry here plus
 * the union member in `@repowise-dev/types/workspace`.
 */

import type { SystemNode } from "@repowise-dev/types";

type SystemNodeKind = SystemNode["kind"];

export interface SystemNodeKindStyle {
  kind: SystemNodeKind;
  /** Display label shown in the node's kind band. */
  label: string;
  /** Tone key into the shared `TONE_STYLES` palette (graph-primitives). */
  tone: string;
}

export const SYSTEM_NODE_KINDS: Record<SystemNodeKind, SystemNodeKindStyle> = {
  service: { kind: "service", label: "Service", tone: "service" },
  frontend: { kind: "frontend", label: "Frontend", tone: "container" },
  worker: { kind: "worker", label: "Worker", tone: "pipeline" },
  library: { kind: "library", label: "Library", tone: "module" },
  external: { kind: "external", label: "External", tone: "external" },
};

const FALLBACK_KIND: SystemNodeKindStyle = {
  kind: "service",
  label: "Service",
  tone: "service",
};

export function nodeKindStyle(kind: string): SystemNodeKindStyle {
  return SYSTEM_NODE_KINDS[kind as SystemNodeKind] ?? FALLBACK_KIND;
}

/** Every node kind, in legend/display order. */
export const NODE_KIND_ORDER: SystemNodeKind[] = ["service", "frontend", "worker", "library", "external"];
