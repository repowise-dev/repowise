// Which layer of the architecture spine a page belongs to, and how to group a
// list of pages by it.
//
// The wiki does not write a page per layer, so a module cannot be parented onto
// one. Instead every module and cycle is stamped at generation time with the
// layer that claims it, and the reader-facing groupings are built from those
// stamps — which means the grouping survives the absence of any layer page.
//
// The order of the layers themselves is a property of the repository, computed
// once from its import graph and recorded on the overview page. It is read from
// there rather than re-derived (or alphabetised, which would put "API" above
// "Runtime" and say something untrue about the dependency direction).

import type { DocPageSummary } from "@repowise-dev/types/docs";

/** The layer id and display name stamped on a page, if any. */
export interface LayerStamp {
  id: string;
  /** Curated display text. Absent on a page stamped before names were kept. */
  name?: string;
}

function text(value: unknown): string | undefined {
  return typeof value === "string" && value ? value : undefined;
}

/**
 * The layer stamp on a page.
 *
 * Read from the promoted columns first, falling back to the metadata blob: a
 * listing serves the columns (the blob is dropped for size), while a row
 * fetched in full carries the blob and a backend older than the columns serves
 * only the blob. `undefined` means no layer claimed this page.
 */
export function readLayerStamp(page: DocPageSummary): LayerStamp | undefined {
  const id = text(page.layer_id) ?? text(page.metadata?.["layer_id"]);
  if (!id) return undefined;
  const name = text(page.layer_name) ?? text(page.metadata?.["layer_name"]);
  return name ? { id, name } : { id };
}

/**
 * The repository's layer spine, top of the dependency order first.
 *
 * Recorded on the overview page at generation time. `layer_order_ids` is the
 * join key; `layer_order` is the older list of display names, kept as a
 * fallback for wikis written before the ids were stored.
 */
export function readLayerOrder(
  page: { metadata?: Record<string, unknown> } | undefined,
): string[] {
  const raw = page?.metadata?.["layer_order_ids"] ?? page?.metadata?.["layer_order"];
  return Array.isArray(raw) ? raw.filter((x): x is string => typeof x === "string") : [];
}

export interface LayerGroup {
  id: string;
  /** What the row is called: the curated name, or the id when none was kept. */
  label: string;
  pages: DocPageSummary[];
}

export interface LayerGrouping {
  /** One per layer that actually claimed a page, in spine order. */
  groups: LayerGroup[];
  /** Pages no layer claimed, in the order they were given. */
  ungrouped: DocPageSummary[];
  /** How many pages carried a stamp at all. */
  stamped: number;
  /** Stamped layer ids the spine does not list, in first-seen order. */
  offSpine: string[];
}

/**
 * Bucket pages by the layer stamped on them, ordered by the given spine.
 *
 * Pure and quiet: it reports what it found (including the ways the inputs
 * disagree) and leaves the complaining to the caller, which knows whether the
 * disagreement is worth a reader's attention.
 *
 * A layer the spine does not list still gets a group — sorted after the ones it
 * does list — because dropping the group would drop the pages in it.
 */
export function groupPagesByLayer(
  pages: readonly DocPageSummary[],
  spine: readonly string[],
): LayerGrouping {
  const rank = new Map(spine.map((id, i) => [id, i]));
  const groups = new Map<string, LayerGroup>();
  const ungrouped: DocPageSummary[] = [];
  const offSpine: string[] = [];
  let stamped = 0;

  for (const page of pages) {
    const layer = readLayerStamp(page);
    if (!layer) {
      ungrouped.push(page);
      continue;
    }
    stamped += 1;
    if (!rank.has(layer.id) && !groups.has(layer.id)) offSpine.push(layer.id);
    const existing = groups.get(layer.id);
    if (existing) {
      existing.pages.push(page);
      // The first member that carries a curated name gets to name the row; the
      // id is only ever a stand-in for one.
      if (existing.label === existing.id && layer.name) existing.label = layer.name;
    } else {
      groups.set(layer.id, { id: layer.id, label: layer.name ?? layer.id, pages: [page] });
    }
  }

  const ordered = [...groups.values()].sort((a, b) => {
    // Off-spine layers sort last, then by label so their order is at least
    // stable rather than dependent on which page happened to come first.
    const ra = rank.get(a.id) ?? Number.MAX_SAFE_INTEGER;
    const rb = rank.get(b.id) ?? Number.MAX_SAFE_INTEGER;
    return ra - rb || a.label.localeCompare(b.label);
  });

  return { groups: ordered, ungrouped, stamped, offSpine };
}
