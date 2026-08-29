/**
 * How a call edge got into the graph, in words a reader can act on.
 *
 * The only place the indexer's two closed vocabularies — `resolution_origin`
 * and `termination` — become English, so no two surfaces describe one edge
 * differently. Per design rule 10, only the `name_match` tier is ever marked:
 * a `same_file` edge is not news, an edge resting on a shared name is.
 *
 * Pure data, no React. Lookups are module-scope, so a per-row call costs one
 * property read.
 */

import type { FlowTermination, ResolutionOrigin } from "@repowise-dev/types/graph";

/**
 * `direct` and `scoped` differ in where the evidence came from, not in how
 * good it is: both required a real language rule to reach the target.
 * `name_match` had no such rule. That is the one line worth drawing on screen.
 */
export type OriginTier = "direct" | "scoped" | "name_match";

export interface OriginDescriptor {
  /** Terse, for a chip or a `title`. Sentence case, no trailing period. */
  label: string;
  /** One clause naming the evidence. Reads after "resolved because ". */
  because: string;
  tier: OriginTier;
}

/** `satisfies` below fails the build when the indexer adds a word and nobody
 *  writes its label, which would otherwise render as "unrecognised" forever. */
const ORIGINS = {
  // --- direct: the calling file, or the caller's own class ---------------
  same_file: {
    label: "Same file",
    because: "the target is defined in the calling file",
    tier: "direct",
  },
  self_scope: {
    label: "Own class",
    because: "the call names self/this and the caller's class declares it",
    tier: "direct",
  },
  enclosing_class: {
    label: "Own class",
    because: "a bare call bound to the caller's own class",
    tier: "direct",
  },
  receiver_same_file: {
    label: "Receiver in file",
    because: "the receiver names a class in the calling file",
    tier: "direct",
  },
  receiver_typed_same_file: {
    label: "Inferred type, same file",
    because: "the receiver's declared type is a class in this file",
    tier: "direct",
  },
  receiver_field_same_file: {
    label: "Field type, same file",
    because: "the receiver is a field whose type is a class in this file",
    tier: "direct",
  },
  receiver_framework_same_file: {
    label: "Framework type, same file",
    because: "a framework decorator retyped the receiver, and that class is in this file",
    tier: "direct",
  },
  return_type_same_file: {
    label: "Return type, same file",
    because: "the inner call's declared return type is a class in this file",
    tier: "direct",
  },

  // --- scoped: an import, a package, a module, a supertype ---------------
  same_package: {
    label: "Same package",
    because: "the target is a sibling file needing no import",
    tier: "scoped",
  },
  import_scoped: {
    label: "Imported",
    because: "the name was imported from the defining file",
    tier: "scoped",
  },
  receiver_same_package: {
    label: "Receiver in package",
    because: "the receiver is a class in the same package",
    tier: "scoped",
  },
  package_alias: {
    label: "Package alias",
    because: "the call is qualified by a package the repo declares",
    tier: "scoped",
  },
  module_alias: {
    label: "Module alias",
    because: "the receiver is an imported module",
    tier: "scoped",
  },
  crate_root: {
    label: "Crate root",
    because: "the reference is crate-scoped",
    tier: "scoped",
  },
  receiver_import: {
    label: "Receiver imported",
    because: "the receiver's class was found in an imported file",
    tier: "scoped",
  },
  import_merged: {
    label: "Imported, file unattributed",
    because: "the name is in one of the imported files, and we cannot say which",
    tier: "scoped",
  },
  scoped_name: {
    label: "Qualified at the call site",
    because: "the call names the class, and that class declares this method",
    tier: "scoped",
  },
  same_target: {
    label: "Same build target",
    because: "the target is a sibling translation unit of the same build target",
    tier: "scoped",
  },
  receiver_typed_same_package: {
    label: "Inferred type, same package",
    because: "the receiver's declared type is a class in the same package",
    tier: "scoped",
  },
  receiver_typed_import: {
    label: "Inferred type, imported",
    because: "the receiver's declared type was found in an imported file",
    tier: "scoped",
  },
  receiver_field_same_package: {
    label: "Field type, same package",
    because: "the receiver is a field whose type is a class in the same package",
    tier: "scoped",
  },
  receiver_field_import: {
    label: "Field type, imported",
    because: "the receiver is a field whose type was found in an imported file",
    tier: "scoped",
  },
  receiver_framework_same_package: {
    label: "Framework type, same package",
    because: "a framework decorator retyped the receiver, and that class is in the same package",
    tier: "scoped",
  },
  receiver_framework_import: {
    label: "Framework type, imported",
    because: "a framework decorator retyped the receiver, and that class was found in an imported file",
    tier: "scoped",
  },
  return_type_same_package: {
    label: "Return type, same package",
    because: "the inner call's declared return type is a class in the same package",
    tier: "scoped",
  },
  return_type_import: {
    label: "Return type, imported",
    because: "the inner call's declared return type was found in an imported file",
    tier: "scoped",
  },
  self_inherited: {
    label: "Inherited",
    because: "the caller's class does not declare it but one ancestor does",
    tier: "scoped",
  },
  enclosing_inherited: {
    label: "Inherited",
    because: "a bare call the caller's class inherits from one ancestor",
    tier: "scoped",
  },

  // --- name_match: no scope evidence, only a name -----------------------
  receiver_global: {
    label: "Name match",
    because: "that class and method pair exists somewhere in the repo",
    tier: "name_match",
  },
  receiver_typed_global: {
    label: "Name match",
    because: "the inferred type and method pair exists somewhere in the repo",
    tier: "name_match",
  },
  receiver_field_global: {
    label: "Name match",
    because: "the field's type and method pair exists somewhere in the repo",
    tier: "name_match",
  },
  receiver_framework_global: {
    label: "Name match",
    because: "the framework type and method pair exists somewhere in the repo",
    tier: "name_match",
  },
  return_type_global: {
    label: "Name match",
    because: "the returned type and method pair exists somewhere in the repo",
    tier: "name_match",
  },
  global_unique: {
    label: "Name match",
    because: "the name is unique across the repo, so nothing else could match",
    tier: "name_match",
  },
} as const satisfies Record<ResolutionOrigin, OriginDescriptor>;

/** An index outlives the bundle reading it, so a newer indexer can stamp a
 *  word this build predates. We know it resolved and not how, so it never
 *  wears the name-match mark. */
const UNRECOGNISED: OriginDescriptor = {
  label: "Resolved",
  because: "this build does not recognise how it was resolved",
  tier: "scoped",
};

/** Describe an origin. Never throws; unknown words degrade rather than blank. */
export function originDescriptor(origin: string | null | undefined): OriginDescriptor | null {
  if (!origin) return null;
  return (ORIGINS as Record<string, OriginDescriptor>)[origin] ?? UNRECOGNISED;
}

/** The one predicate a surface should branch on. Everything else is ordinary
 *  resolution and gets no decoration. */
export function isNameMatch(origin: string | null | undefined): boolean {
  return originDescriptor(origin)?.tier === "name_match";
}

export interface TerminationCopy {
  /** Terse, for a chip. Sentence case, no trailing period. */
  label: string;
  /** The full claim, and the only place the distinction actually survives. */
  sentence: string;
  /** A gap in what we know, rather than a fact about the code. */
  isLimit: boolean;
}

const TERMINATIONS = {
  depth_limit: {
    label: "Depth limit",
    sentence: "The trace stopped at its depth limit. Execution continues past this point.",
    isLimit: true,
  },
  callees_truncated: {
    label: "Too many callees",
    sentence:
      "This symbol has more outgoing calls than the trace reads, so the next step is unknown rather than absent.",
    isLimit: true,
  },
  cycle: {
    label: "Cycle",
    sentence:
      "Every next step was already on this path. That is recursion or a mutual call, not the end of execution.",
    isLimit: false,
  },
  confidence_filtered: {
    label: "Below threshold",
    sentence:
      "Every next step rested on a name match alone, which we will not assert as a call.",
    isLimit: true,
  },
  excluded_target: {
    label: "Test or fixture",
    sentence: "Every next step was a test, demo or fixture, which traces do not follow.",
    isLimit: false,
  },
  no_callees: {
    label: "No calls recorded",
    sentence:
      "No outgoing calls were recorded here. That can mean this symbol calls nothing, or that we could not resolve what it calls.",
    isLimit: true,
  },
} as const satisfies Record<FlowTermination, TerminationCopy>;

/** Describe a termination. Returns null for an index that carries none. */
export function terminationCopy(reason: string | null | undefined): TerminationCopy | null {
  if (!reason) return null;
  return (TERMINATIONS as Record<string, TerminationCopy>)[reason] ?? null;
}
