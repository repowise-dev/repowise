// Gather the pages a Present deck is built from.
//
// `buildPresentModel` needs bodies, but the host's page list no longer carries
// them: on a large wiki that would be tens of megabytes loaded before anything
// renders. A deck only ever draws on a couple of dozen pages, though — the
// overview, the architecture diagram, the first few layers and modules, and the
// guided tour's landmarks — so they can be fetched when Present is opened.
//
// The selection rules live here rather than being re-derived from the builder's
// caps: same constants, same ordering functions, one definition each.

import type { DocPage, DocPageSummary } from "@repowise-dev/types/docs";
import {
  MAX_LAYER_SLIDES,
  MAX_MODULE_SLIDES,
  orderedLayers,
  readTour,
} from "./build-present-model";

/** A row is usable as a `DocPage` once it carries both of the heavy fields. */
function isHydrated(page: DocPageSummary): page is DocPage {
  return typeof page.content === "string" && page.metadata !== undefined;
}

/**
 * Resolve the deck's source pages, fetching bodies only for rows that arrived
 * without one. Returns an empty list when there is nothing to present, which
 * is the same condition `canPresent` reports.
 */
export async function loadPresentPages(
  pages: readonly DocPageSummary[],
  fetchPage: (pageId: string) => Promise<DocPage>,
): Promise<DocPage[]> {
  const hydrate = (page: DocPageSummary): Promise<DocPage> =>
    isHydrated(page) ? Promise.resolve(page) : fetchPage(page.id);

  const overviewRow = pages.find((p) => p.page_type === "repo_overview");
  if (!overviewRow) return [];
  // The overview comes first on its own: the guided tour and the layer order
  // are read out of its metadata, and both decide what else is worth fetching.
  const overview = await hydrate(overviewRow);

  const wanted: DocPageSummary[] = [overview];

  const arch = pages.find((p) => p.page_type === "architecture_diagram");
  if (arch) wanted.push(arch);

  wanted.push(...orderedLayers(pages, overview).slice(0, MAX_LAYER_SLIDES));

  // "Richest first" — the builder proxies richness by body length, which the
  // summary reports as content_chars. A row with no count sorts last rather
  // than winning on a NaN comparison.
  wanted.push(
    ...pages
      .filter((p) => p.page_type === "module_page")
      .sort((a, b) => (b.content_chars ?? 0) - (a.content_chars ?? 0))
      .slice(0, MAX_MODULE_SLIDES),
  );

  const byPath = new Map(pages.map((p) => [p.target_path, p]));
  for (const stop of readTour(overview)) {
    const hit = stop.target_path ? byPath.get(stop.target_path) : undefined;
    if (hit) wanted.push(hit);
  }

  const unique = [...new Map(wanted.map((p) => [p.id, p])).values()];
  return Promise.all(unique.map(hydrate));
}
