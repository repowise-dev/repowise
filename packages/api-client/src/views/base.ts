/**
 * What every view kind shares: identity and provenance checks, and the shape
 * of a kind's registry entry.
 */

import {
  VIEW_COVERAGE_METRICS,
  VIEW_SCHEMA_VERSIONS,
  type ViewCoverage,
  type ViewKind,
  type ViewPartBase,
  type ViewPartsByKind,
  type ViewProvenance,
  type ViewSource,
} from "@repowise-dev/types/views";

import {
  basis,
  bool,
  constant,
  matches,
  none,
  nullable,
  obj,
  oneOf,
  oneOfNumbers,
  ratio,
  str,
  type Check,
  type Spec,
} from "../validation";

export const PART_ID = /^[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}$/;
export const partId = matches(PART_ID, "a part id (letters, digits, _ . : -, at most 64)");

export const SOURCE_SPEC: Spec<ViewSource> = {
  required: {
    artifact_id: str,
    tool_name: str,
    args_hash: matches(/^[0-9a-f]{8,128}$/, "a lowercase hex digest"),
  },
  optional: none,
};

export const COVERAGE_SPEC: Spec<ViewCoverage> = {
  required: { metric: oneOf(VIEW_COVERAGE_METRICS), ratio },
  optional: none,
};

/** The fields of `ViewPartBase`, with `kind` pinned to one kind. */
export function baseSpec<K extends string>(kind: K): Spec<ViewPartBase<K>> {
  return {
    required: {
      schema_version: oneOfNumbers(VIEW_SCHEMA_VERSIONS),
      kind: constant(kind),
      id: partId,
      title: str,
    },
    optional: { summary: str },
  };
}

export const PROVENANCE_SPEC: Spec<ViewProvenance> = {
  required: {
    source: obj(SOURCE_SPEC),
    basis,
    stale: bool,
    as_of: nullable(matches(/^[0-9a-f]{7,64}$/, "a lowercase commit sha")),
  },
  optional: { coverage: obj(COVERAGE_SPEC) },
};

/** An id another part names, and the path (relative to the part) it is named at. */
export type PartReference = readonly [id: string, path: string];

/** One view kind's entry in `VIEW_REGISTRY`. */
export interface ViewKindEntry<K extends ViewKind> {
  /** Checks one part of this kind on its own. */
  check: Check;
  /**
   * A data part carries provenance and is what other parts may name. Typed
   * from the part, so it cannot disagree with whether the kind has `source`.
   */
  isData: ViewPartsByKind[K] extends ViewProvenance ? true : false;
  /** Ids of data parts this part names; each must be in the same list. */
  references?: (part: ViewPartsByKind[K]) => PartReference[];
}
