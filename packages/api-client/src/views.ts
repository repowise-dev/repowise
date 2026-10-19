/**
 * Chat view parts: the schema re-exported, plus a dependency-free validator a
 * renderer runs on a payload before trusting it.
 *
 * `VIEW_REGISTRY` is the kind registry: one entry per `ViewKind`, each from its
 * own module under `./views/`, declaring the kind's check, whether it is a
 * data part, and which other parts it names. The list-level rules below only
 * iterate the registry, so a new kind never edits them. A kind in
 * `ViewPartsByKind` without an entry here fails to compile.
 */

import {
  VIEW_LIMITS,
  VIEW_SCHEMA_VERSION,
  VIEW_SCHEMA_VERSIONS,
  type ViewKind,
  type ViewPart,
  type ViewPartsByKind,
} from "@repowise-dev/types/views";

import { duplicates, isRec } from "./validation";
import type { PartReference, ViewKindEntry } from "./views/base";
import { CHART_KIND } from "./views/chart";
import { SECTION_KIND } from "./views/section";
import { TABLE_KIND } from "./views/table";

export * from "@repowise-dev/types/views";
export type { PartReference, ViewKindEntry } from "./views/base";

export const VIEW_REGISTRY: { [K in ViewKind]: ViewKindEntry<K> } = {
  table: TABLE_KIND,
  chart: CHART_KIND,
  section: SECTION_KIND,
};
export const VIEW_KINDS = Object.keys(VIEW_REGISTRY) as ViewKind[];

export type ViewValidation =
  | { ok: true; view: ViewPart }
  | { ok: false; errors: string[] };

export type ViewListValidation =
  | { ok: true; views: ViewPart[] }
  | { ok: false; errors: string[] };

const entryOf = <K extends ViewKind>(part: ViewPartsByKind[K]): ViewKindEntry<K> =>
  VIEW_REGISTRY[part.kind as K];

function checkPart(input: unknown, path: string, errors: string[]): void {
  const kind = isRec(input) && Object.hasOwn(input, "kind") ? input.kind : undefined;
  if (typeof kind !== "string" || !Object.hasOwn(VIEW_REGISTRY, kind)) {
    errors.push(`${path}.kind: expected one of ${VIEW_KINDS.join(", ")}`);
    return;
  }
  VIEW_REGISTRY[kind as ViewKind].check(input, path, errors);
}

const dataIdsOf = (views: ViewPart[]): Set<string> =>
  new Set(views.filter((v) => entryOf(v).isData).map((v) => v.id));

/** References a part makes that are not data parts in `dataIds`, as errors. */
function unresolved(part: ViewPart, dataIds: ReadonlySet<string>, path: string): string[] {
  const refs: PartReference[] = entryOf(part).references?.(part as never) ?? [];
  return refs
    .filter(([id]) => !dataIds.has(id))
    .map(([id, at]) => `${path}${at}: no data part with id ${id}`);
}

/**
 * Validates one part. Errors name every problem the checks reach; a value of
 * the wrong shape stops the checks beneath it.
 */
export function validateViewPart(input: unknown): ViewValidation {
  const errors: string[] = [];
  checkPart(input, "view", errors);
  return errors.length ? { ok: false, errors } : { ok: true, view: input as ViewPart };
}

/**
 * Validates the parts of one turn or report together: each part, unique ids,
 * and that every id a part names is a data part in the same list.
 */
export function validateViewParts(input: unknown): ViewListValidation {
  if (!Array.isArray(input)) return { ok: false, errors: ["views: expected an array"] };
  if (input.length > VIEW_LIMITS.parts) {
    return { ok: false, errors: [`views: more than ${VIEW_LIMITS.parts} items`] };
  }
  const errors: string[] = [];
  input.forEach((part, i) => checkPart(part, `views[${i}]`, errors));
  if (errors.length) return { ok: false, errors };

  const views = input as ViewPart[];
  for (const id of duplicates(views.map((v) => v.id))) errors.push(`views: duplicate id ${id}`);
  const dataIds = dataIdsOf(views);
  views.forEach((v, i) => errors.push(...unresolved(v, dataIds, `views[${i}]`)));
  return errors.length ? { ok: false, errors } : { ok: true, views };
}

const TABLE_FIELDS = ["id", "title", "summary", "source", "basis", "coverage", "stale", "as_of"];
const TABLE_DATA = ["columns", "rows", "total_rows", "truncated"];

/**
 * A chart or table this client cannot check as sent (a newer chart type or
 * field) kept as its data table, so newer parts degrade instead of vanishing.
 * Null when even the table does not hold.
 */
export function asDataTable(input: unknown): ViewPart | null {
  if (!isRec(input) || (input.kind !== "chart" && input.kind !== "table")) return null;
  const data = input.kind === "chart" ? input.data : input;
  if (!isRec(data)) return null;
  const known = (VIEW_SCHEMA_VERSIONS as readonly unknown[]).includes(input.schema_version);
  const table: Record<string, unknown> = {
    schema_version: known ? input.schema_version : VIEW_SCHEMA_VERSION,
    kind: "table",
  };
  for (const key of TABLE_FIELDS) if (Object.hasOwn(input, key)) table[key] = input[key];
  for (const key of TABLE_DATA) if (Object.hasOwn(data, key)) table[key] = data[key];
  const result = validateViewPart(table);
  return result.ok ? result.view : null;
}

/**
 * Keeps what a renderer can show from an untrusted list: the valid parts among
 * the first `VIEW_LIMITS.parts` (a chart it cannot draw as its table), the
 * first part for each id, and only the parts whose references all resolve to
 * a kept data part.
 */
export function parseViewParts(input: unknown): ViewPart[] {
  if (!Array.isArray(input)) return [];
  const seen = new Set<string>();
  const kept = input.slice(0, VIEW_LIMITS.parts).flatMap((item) => {
    const checked = validateViewPart(item);
    const degraded = checked.ok ? null : asDataTable(item);
    const result: ViewValidation = degraded ? { ok: true, view: degraded } : checked;
    if (!result.ok || seen.has(result.view.id)) return [];
    seen.add(result.view.id);
    return [result.view];
  });
  const dataIds = dataIdsOf(kept);
  return kept.filter((v) => unresolved(v, dataIds, "").length === 0);
}
