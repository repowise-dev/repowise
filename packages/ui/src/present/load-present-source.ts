// Gather the pages a Present deck is built from.
//
// `buildPresentModel` needs bodies, but the host's page list does not carry
// them: on a large wiki that would be tens of megabytes loaded before anything
// renders. A deck only draws on the overview and a handful of major parts, so
// those bodies are fetched when Present is opened.
//
// A major part is a top-level section of the wiki outline. The biggest ones
// (most module pages nested beneath them, then most written) are presented,
// in outline order so the deck follows the documentation tree.

import type { DocPage, DocPageSummary } from "@repowise-dev/types/docs";
import type { PresentSource } from "./types";

const MAX_PARTS = 6;

/** A row is usable as a `DocPage` once it carries both of the heavy fields. */
function isHydrated(page: DocPageSummary): page is DocPage {
  return typeof page.content === "string" && page.metadata !== undefined;
}

/**
 * Top-level module pages, biggest first. A module whose parent is not itself
 * a module page (the overview, or no outline at all) is top-level; its size is
 * the number of module pages nested beneath it.
 */
function rankParts<T extends DocPageSummary>(pages: readonly T[]): T[] {
  const modules = pages.filter((p) => p.page_type === "module_page");
  const byId = new Map(modules.map((p) => [p.id, p]));
  const parentOf = (p: T | undefined) =>
    p?.parent_page_id && byId.has(p.parent_page_id) ? p.parent_page_id : undefined;

  const nested = new Map<string, number>();
  for (const page of modules) {
    // Bounded walk: an outline is shallow, and a malformed cycle must not hang.
    let parent = parentOf(page);
    for (let depth = 0; parent && depth < modules.length; depth++) {
      nested.set(parent, (nested.get(parent) ?? 0) + 1);
      parent = parentOf(byId.get(parent));
    }
  }
  return modules
    .filter((p) => !parentOf(p))
    .sort(
      (a, b) =>
        (nested.get(b.id) ?? 0) - (nested.get(a.id) ?? 0) ||
        (b.content_chars ?? 0) - (a.content_chars ?? 0),
    );
}

/**
 * Resolve the deck's source pages, fetching bodies only for rows that arrived
 * without one. Returns null when there is nothing to present, which is the
 * same condition `canPresent` reports.
 */
export async function loadPresentSource(
  pages: readonly DocPageSummary[],
  fetchPage: (pageId: string) => Promise<DocPage>,
): Promise<PresentSource | null> {
  const hydrate = (page: DocPageSummary): Promise<DocPage> =>
    isHydrated(page) ? Promise.resolve(page) : fetchPage(page.id);

  const overviewRow = pages.find((p) => p.page_type === "repo_overview");
  if (!overviewRow) return null;

  const ranked = rankParts(pages);
  // Outline order for the story; rows without a position keep their rank.
  const chosen = ranked
    .slice(0, MAX_PARTS)
    .map((page, rank) => ({ page, rank }))
    .sort(
      (a, b) =>
        (a.page.display_order ?? Infinity) - (b.page.display_order ?? Infinity) ||
        a.rank - b.rank,
    )
    .map(({ page }) => page);

  const [overview, ...parts] = await Promise.all([overviewRow, ...chosen].map(hydrate));
  const pageIdByPath = new Map(
    pages.filter((p) => p.page_type === "file_page").map((p) => [p.target_path, p.id]),
  );
  return { overview: overview!, parts, totalParts: ranked.length, pageIdByPath };
}
