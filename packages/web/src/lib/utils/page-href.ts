import { fileEntityPath } from "@repowise-dev/ui/shared/entity";

/**
 * Resolve the best in-app destination for a wiki page id.
 *
 * File pages route to the canonical file entity page; everything else opens
 * inside the docs SPA (`/docs?page=`) instead of the standalone wiki route,
 * so navigation keeps the tree/reading context.
 */
export function pageHref(repoId: string, pageId: string): string {
  if (pageId.startsWith("file_page:")) {
    return fileEntityPath(`/repos/${repoId}`, pageId.slice("file_page:".length));
  }
  return `/repos/${repoId}/docs?page=${encodeURIComponent(pageId)}`;
}
