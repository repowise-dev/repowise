import { fileEntityPath } from "../shared/entity/routes";

export type AttentionItemType =
  | "stale_decision"
  | "knowledge_silo"
  | "ungoverned_hotspot"
  | "dead_code"
  | "proposed_decision";

export interface AttentionItem {
  id: string;
  type: AttentionItemType;
  title: string;
  description: string;
  severity: "high" | "medium" | "low";
  /** What the item points at — a decision id, file path, owner, … Used to
   *  deep-link straight to the offending entity. */
  target_id?: string;
  href?: string;
}

/**
 * Where an attention item points.
 *
 * Lives in a plain module rather than beside the panel that renders it,
 * because two surfaces need it and only one of them is a client component.
 * Importing it from `attention-panel` worked at compile time and failed at
 * runtime with "attempted to call getDefaultHref() from the server but
 * getDefaultHref is on the client" — a `"use client"` file exports components
 * to the server, not callable functions.
 *
 * Kept in one place because each branch is a per-type routing decision (a silo
 * wants the owners view filtered to its path, an ungoverned hotspot wants the
 * file page), so a second caller re-deriving them by hand gets most of them
 * wrong.
 */
export function getDefaultHref(item: AttentionItem, prefix: string): string {
  const target = item.target_id;
  switch (item.type) {
    case "stale_decision":
    case "proposed_decision":
      // Deep-link to the specific decision when we know which one.
      return target
        ? `${prefix}/decisions/${encodeURIComponent(target)}`
        : `${prefix}/decisions`;
    case "knowledge_silo":
      // The silo target is the owning module/path — surface it on the owners
      // view so the offending area is preselected.
      return target ? `${prefix}/owners?path=${encodeURIComponent(target)}` : `${prefix}/owners`;
    case "ungoverned_hotspot":
      // Target is the hotspot's file path → open its file entity page.
      return target ? fileEntityPath(prefix, target) : `${prefix}/code-health?tab=triage`;
    case "dead_code":
      return `${prefix}/code-health?tab=dead-code`;
    default:
      return prefix;
  }
}
