/** Checks every view part shares: identity, and for data parts, provenance. */

import {
  VIEW_COVERAGE_METRICS,
  VIEW_SCHEMA_VERSION,
  type ViewCoverage,
  type ViewPartBase,
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
  ratio,
  str,
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
      schema_version: constant(VIEW_SCHEMA_VERSION),
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

/** Ids listed more than once, for a uniqueness error. */
export function duplicates(ids: unknown[]): unknown[] {
  return ids.filter((id, i) => ids.indexOf(id) !== i);
}
