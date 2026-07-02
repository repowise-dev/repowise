import { apiGet, apiPatch, apiPost } from "./client";
import type { PageResponse, PageVersionResponse } from "./types";

export async function listPages(
  repoId: string,
  opts?: { page_type?: string; sort_by?: string; order?: string; limit?: number; offset?: number },
): Promise<PageResponse[]> {
  return apiGet<PageResponse[]>("/api/pages", { repo_id: repoId, ...opts });
}

/** Fetch all pages for a repo, auto-paginating through the 500-item backend limit. */
export async function listAllPages(
  repoId: string,
  opts?: { page_type?: string; sort_by?: string; order?: string },
): Promise<PageResponse[]> {
  const PAGE_SIZE = 500;
  const all: PageResponse[] = [];
  let offset = 0;

  while (true) {
    const batch = await listPages(repoId, { ...opts, limit: PAGE_SIZE, offset });
    all.push(...batch);
    if (batch.length < PAGE_SIZE) break;
    offset += PAGE_SIZE;
  }

  return all;
}

/** Get page by its ID using the query-param endpoint (avoids path conflicts) */
export async function getPageById(pageId: string): Promise<PageResponse> {
  return apiGet<PageResponse>("/api/pages/lookup", { page_id: pageId });
}

/** Get page versions by ID */
export async function getPageVersions(
  pageId: string,
  limit = 50,
): Promise<PageVersionResponse[]> {
  return apiGet<PageVersionResponse[]>("/api/pages/lookup/versions", {
    page_id: pageId,
    limit,
  });
}

/** Set or clear the human-curated note pinned above a page's generated content. */
export async function updatePageNotes(
  pageId: string,
  humanNotes: string | null,
): Promise<PageResponse> {
  return apiPatch<PageResponse>(
    `/api/pages/lookup/notes?page_id=${encodeURIComponent(pageId)}`,
    { human_notes: humanNotes },
  );
}

/**
 * Force-regenerate a page by ID. Pass `style` to regenerate this page in a
 * specific wiki style (per-page override); omit it to use the repo's style.
 */
export async function regeneratePage(
  pageId: string,
  style?: string,
): Promise<{ job_id: string }> {
  return apiPost<{ job_id: string }>("/api/pages/lookup/regenerate", undefined, undefined, {
    page_id: pageId,
    ...(style ? { style } : {}),
  });
}
