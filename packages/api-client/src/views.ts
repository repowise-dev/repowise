/**
 * Chat view parts: the schema re-exported, plus a dependency-free validator a
 * renderer runs on a payload before trusting it.
 *
 * `VIEW_CHECKS` is the kind registry: one check per `ViewKind`, each in its
 * own module under `./views/`. Adding a kind to `ViewPartsByKind` without a
 * check here fails to compile. Single parts are checked on their own; the
 * references sections make to other parts are checked over the whole list.
 */

import {
  VIEW_KINDS,
  VIEW_LIMITS,
  type SectionView,
  type ViewKind,
  type ViewPart,
} from "@repowise-dev/types/views";

import { isRec, type Check } from "./validation";
import { duplicates } from "./views/base";
import { chart } from "./views/chart";
import { section } from "./views/section";
import { table } from "./views/table";

export * from "@repowise-dev/types/views";

export const VIEW_CHECKS: { [K in ViewKind]: Check } = { table, chart, section };

export type ViewValidation =
  | { ok: true; view: ViewPart }
  | { ok: false; errors: string[] };

export type ViewListValidation =
  | { ok: true; views: ViewPart[] }
  | { ok: false; errors: string[] };

function checkPart(input: unknown, path: string, errors: string[]): void {
  const kind = isRec(input) && Object.hasOwn(input, "kind") ? input.kind : undefined;
  if (typeof kind !== "string" || !(VIEW_KINDS as readonly string[]).includes(kind)) {
    errors.push(`${path}.kind: expected one of ${VIEW_KINDS.join(", ")}`);
    return;
  }
  VIEW_CHECKS[kind as ViewKind](input, path, errors);
}

/** Ids a section names that are not data parts in `dataIds`, with their paths. */
function unresolved(view: SectionView, dataIds: ReadonlySet<string>, path: string): string[] {
  const named = [
    ...view.view_ids.map((id, j) => [id, `${path}.view_ids[${j}]`] as const),
    ...view.narrative.flatMap((block, j) =>
      block.cites.map((id, k) => [id, `${path}.narrative[${j}].cites[${k}]`] as const),
    ),
  ];
  return named
    .filter(([id]) => !dataIds.has(id))
    .map(([id, at]) => `${at}: no table or chart part with id ${id}`);
}

const dataIdsOf = (views: ViewPart[]): Set<string> =>
  new Set(views.filter((v) => v.kind !== "section").map((v) => v.id));

/** Validates one part, returning every problem found rather than the first. */
export function validateViewPart(input: unknown): ViewValidation {
  const errors: string[] = [];
  checkPart(input, "view", errors);
  return errors.length ? { ok: false, errors } : { ok: true, view: input as ViewPart };
}

/**
 * Validates the parts of one turn or report together: each part, unique ids,
 * and that every id a section names is a table or chart in the same list.
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
  for (const id of new Set(duplicates(views.map((v) => v.id)))) {
    errors.push(`views: duplicate id ${String(id)}`);
  }
  const dataIds = dataIdsOf(views);
  views.forEach((v, i) => {
    if (v.kind === "section") errors.push(...unresolved(v, dataIds, `views[${i}]`));
  });
  return errors.length ? { ok: false, errors } : { ok: true, views };
}

/**
 * Keeps what a renderer can show from an untrusted list: the valid parts among
 * the first `VIEW_LIMITS.parts`, the first part for each id, and only the
 * sections whose references all resolve to a kept table or chart.
 */
export function parseViewParts(input: unknown): ViewPart[] {
  if (!Array.isArray(input)) return [];
  const seen = new Set<string>();
  const kept = input.slice(0, VIEW_LIMITS.parts).flatMap((item) => {
    const result = validateViewPart(item);
    if (!result.ok || seen.has(result.view.id)) return [];
    seen.add(result.view.id);
    return [result.view];
  });
  const dataIds = dataIdsOf(kept);
  return kept.filter((v) => v.kind !== "section" || unresolved(v, dataIds, "").length === 0);
}
