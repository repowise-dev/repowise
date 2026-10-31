import type { C4Relation } from "./types";

/**
 * "imports · 1,359". The count is the number of code links the relation rolls
 * up; synthetic relations (an L1 actor or the system's tech stack) carry no
 * coupling and no meaningful count, so they show the verb alone. The backend
 * ships a readable verb; the bare edge type covers older payloads.
 */
export function relationLabel(relation: C4Relation): string {
  const verb = relation.label || (relation.edge_types[0] ?? "");
  return verb && relation.coupling ? `${verb} · ${relation.edge_count.toLocaleString("en-US")}` : verb;
}
