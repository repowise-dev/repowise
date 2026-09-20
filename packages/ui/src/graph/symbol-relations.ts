import type {
  SymbolRelationDirection,
  SymbolRelationGroup,
} from "@repowise-dev/types/symbols";

/**
 * The one place a symbol-graph `edge_type` becomes a heading a reader sees.
 *
 * The engine's `SYMBOL_USE_EDGE_TYPES` answers "does something reach this
 * symbol", which is the right question for reachability and the wrong one for
 * a page: a subclass reaches its base without calling it. Rendering all seven
 * kinds under "Called by" told a reader that 1,516 Django models call
 * `Model` — and, because the cut was ranked by confidence under one shared
 * cap, hid the 8 symbols that actually do.
 *
 * Kept beside `edge-provenance.ts` for the same reason: a closed vocabulary
 * crossing the language boundary needs exactly one translation, and a Python
 * test pins these keys against the set they are copied from.
 */

/** Heading per edge type, per side. Both halves of a relation are named, since
 *  "Extends" and "Extended by" are different facts about the same edge. */
const RELATION_LABELS: Record<string, Record<SymbolRelationDirection, string>> = {
  // Class heritage. The inbound side is the one a reader arrives for: it
  // answers "who builds on this".
  extends: { in: "Extended by", out: "Extends" },
  implements: { in: "Implemented by", out: "Implements" },
  // Method heritage. A base method answered by four implementations wants
  // "Implementations", not "Extended by" — the class phrasing reads as
  // nonsense on a method and is the wording half of this fix.
  //
  // The two point opposite ways and the labels have to follow, not read
  // alike. `method_implements` runs implementation -> base, the direction the
  // source declares. `dispatches_to` runs base -> implementation, because it
  // exists to make an implementation reachable from a call landing on the
  // base; verified on a live index, where `BaseProvider.generate` carries its
  // 19 `dispatches_to` edges outbound.
  method_implements: { in: "Implementations", out: "Implements" },
  dispatches_to: { in: "Implements", out: "Implementations" },
  // Framework wiring: the container injects this, so no parser sees a call
  // site. Runs consumer -> provider, so a fixture's inbound edges are the
  // tests it is wired into (388 of them for `client` on this repo).
  framework_binds: { in: "Wired into", out: "Wired to" },
  reads: { in: "Read by", out: "Reads" },
  references: { in: "Referenced by", out: "References" },
};

/** One sentence per group, shown once beneath its heading. Explains why a
 *  relation is not a call, which is the question the grouping raises. */
const GROUP_HINTS: Record<SymbolRelationGroup["group"], string> = {
  heritage: "Inherits or implements. Not a call site.",
  wiring: "Connected by a framework, so no call site exists in the source.",
  reference:
    "Reached without a function call: a handler named in a dispatch table, or a Rust macro invocation.",
};

export function relationLabel(
  edgeType: string,
  direction: SymbolRelationDirection,
): string {
  const entry = RELATION_LABELS[edgeType];
  if (entry) return entry[direction];
  // An edge type the engine gained and this table has not. Say the raw verb
  // rather than dropping the section: an unnamed relation is still better
  // than one silently filed under "Called by", which is the bug being fixed.
  return direction === "in" ? `${edgeType} (inbound)` : edgeType;
}

export function relationHint(group: SymbolRelationGroup["group"]): string | undefined {
  return GROUP_HINTS[group];
}

/** Edge types this module can name, for the cross-language pin test. */
export const KNOWN_RELATION_EDGE_TYPES: readonly string[] =
  Object.keys(RELATION_LABELS).sort();
