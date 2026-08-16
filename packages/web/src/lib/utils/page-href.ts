import { docsPagePath, fileEntityPath, filePageId } from "@repowise-dev/ui/shared/entity";

/** The `file_page:` id prefix, derived from the builder rather than restated,
 *  so the parse here cannot drift from the construction there. */
const FILE_PAGE_PREFIX = filePageId("");

/**
 * Resolve the best in-app destination for a wiki page id.
 *
 * File pages route to the canonical file entity page; everything else opens
 * inside the docs SPA (`/docs?page=`) instead of the standalone wiki route,
 * so navigation keeps the tree/reading context.
 */
export function pageHref(repoId: string, pageId: string): string {
  const prefix = `/repos/${repoId}`;
  if (pageId.startsWith(FILE_PAGE_PREFIX)) {
    return fileEntityPath(prefix, pageId.slice(FILE_PAGE_PREFIX.length));
  }
  return docsPagePath(prefix, pageId);
}
