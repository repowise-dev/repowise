/**
 * What one box's arrows actually say, in words. Pure, browser-free.
 *
 * The canvas draws a relation as a routed line whose only encoding is a
 * coupling-derived weight; the verb (`ZoomRelation.label`) was carried on the
 * wire and never rendered anywhere, so nothing on the surface said whether an
 * arrow meant "imports" or "changes at the same time as". This module turns the
 * relations incident to a node into the sentence the detail panel prints and
 * the caption the canvas key uses.
 *
 * It also reports what the per-parent cap hides. `drawEdges` shows only the
 * strongest `EDGE_MAX_PER_PARENT`, and on a real index that bites hard: 99 of
 * 218 containers have more relations than the cap, the worst holding 133. A
 * bounded view that does not say it is bounded reads as a complete one.
 */

import { EDGE_MAX_PER_PARENT } from "./constants";
import type { ZoomMap, ZoomRelation } from "./types";

/** The co-changes verb, as `c4_builder/labels.py` spells it. */
export const CO_CHANGES = "co-changes";

export interface VerbCount {
  verb: string;
  count: number;
}

export interface RelationSummary {
  /** Relations incident to the node, in either direction. */
  total: number;
  /** How many the canvas will actually draw, given the per-parent cap. */
  shown: number;
  /** Descending by count, so the dominant verb reads first. */
  verbs: VerbCount[];
  /** Incident relations whose verb is `co-changes`. */
  coChanges: number;
}

/**
 * Index relations by each endpoint once, so a panel that re-renders per
 * selection does not rescan the whole relation list (3,694 on this repo).
 */
export function indexRelationsByNode(map: ZoomMap): Map<string, ZoomRelation[]> {
  const byNode = new Map<string, ZoomRelation[]>();
  const add = (id: string, r: ZoomRelation): void => {
    const bucket = byNode.get(id);
    if (bucket) bucket.push(r);
    else byNode.set(id, [r]);
  };
  for (const r of map.relations) {
    if (r.source_id === r.target_id) continue;
    add(r.source_id, r);
    add(r.target_id, r);
  }
  return byNode;
}

/** Summarise the relations incident to one node. */
export function summarizeRelations(relations: readonly ZoomRelation[]): RelationSummary {
  const counts = new Map<string, number>();
  let coChanges = 0;
  for (const r of relations) {
    counts.set(r.label, (counts.get(r.label) ?? 0) + 1);
    if (r.label === CO_CHANGES) coChanges += 1;
  }
  const verbs = [...counts.entries()]
    .map(([verb, count]) => ({ verb, count }))
    .sort((a, b) => b.count - a.count || a.verb.localeCompare(b.verb));

  return {
    total: relations.length,
    shown: Math.min(relations.length, EDGE_MAX_PER_PARENT),
    verbs,
    coChanges,
  };
}

/**
 * Add or remove one verb from the drawn set, in the map key's chip row.
 *
 * Null means "every verb draws", and emptying the set returns to it rather than
 * to a canvas with no arrows: a filter whose natural resting state hides
 * everything trains people not to touch it. That makes null the only
 * representation of "unfiltered", so callers never have to treat a full set and
 * null as the same thing.
 */
export function toggleVerb(
  selected: ReadonlySet<string> | null,
  verb: string,
): ReadonlySet<string> | null {
  const next = new Set(selected ?? []);
  if (next.has(verb)) next.delete(verb);
  else next.add(verb);
  return next.size === 0 ? null : next;
}

/**
 * The summary as one readable line.
 *
 * On a live index 89% of boxes have a single verb across all their relations,
 * so the common case is "9 relations, all imports" and the enumerated form is
 * the exception rather than the default.
 */
export function describeRelations(summary: RelationSummary): string {
  const { total, verbs } = summary;
  if (total === 0) return "No relations at this level";
  const noun = total === 1 ? "relation" : "relations";
  if (verbs.length === 1) {
    const only = verbs[0]!.verb;
    return total === 1 ? `1 ${only} relation` : `${total} ${noun}, all ${only}`;
  }
  const parts = verbs.map((v) => `${v.count} ${v.verb}`);
  return `${total} ${noun}: ${parts.join(", ")}`;
}

/**
 * What the cap is hiding, or null when nothing is. Kept separate from
 * `describeRelations` so a caller can style the caveat differently from the
 * fact, and so the honest-cap copy exists in exactly one place.
 */
export function describeCap(summary: RelationSummary): string | null {
  if (summary.total <= summary.shown) return null;
  return `Showing the ${summary.shown} strongest`;
}
