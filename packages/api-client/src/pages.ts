import { apiGet, apiPatch, apiPost } from "./client";
import type {
  PageResponse,
  PageSummary,
  PageVersionResponse,
  JobLaunchResponse,
  GenerateCascade,
} from "./types";

/**
 * How much of each page a listing should carry.
 *
 * `full` is every field and stays the default, so no existing caller changes
 * meaning. `summary` drops `content` and `metadata` — 95% of the bytes on a
 * large wiki, and read by nothing that renders a list — in exchange for a
 * `content_chars` count.
 */
export type PageFields = "full" | "summary";

interface ListPagesOpts {
  page_type?: string;
  sort_by?: string;
  order?: string;
  limit?: number;
  offset?: number;
}

export async function listPages(
  repoId: string,
  opts: ListPagesOpts & { fields: "summary" },
): Promise<PageSummary[]>;
export async function listPages(
  repoId: string,
  opts?: ListPagesOpts & { fields?: "full" },
): Promise<PageResponse[]>;
export async function listPages(
  repoId: string,
  opts?: ListPagesOpts & { fields?: PageFields },
): Promise<PageSummary[]> {
  return apiGet<PageSummary[]>("/api/pages", { repo_id: repoId, ...opts });
}

interface ListAllPagesOpts {
  page_type?: string;
  sort_by?: string;
  order?: string;
}

/** Fetch all pages for a repo, auto-paginating through the 500-item backend limit. */
export async function listAllPages(
  repoId: string,
  opts: ListAllPagesOpts & { fields: "summary" },
): Promise<PageSummary[]>;
export async function listAllPages(
  repoId: string,
  opts?: ListAllPagesOpts & { fields?: "full" },
): Promise<PageResponse[]>;
export async function listAllPages(
  repoId: string,
  opts?: ListAllPagesOpts & { fields?: PageFields },
): Promise<PageSummary[]> {
  // Batch size follows the shape, because the two differ by ~15x per row. 500
  // full rows is ~6 MB; 500 summary rows is ~0.4 MB, so the same number of
  // trips would be spent almost entirely on latency. Both stay under a couple
  // of megabytes per response.
  const fields = opts?.fields ?? "full";
  const PAGE_SIZE = fields === "summary" ? 2000 : 500;
  const all: PageSummary[] = [];
  let offset = 0;

  while (true) {
    // Kept sequential on purpose. Several concurrent multi-megabyte listings is
    // the request shape behind a past memory incident; the fix for a slow
    // listing is a smaller payload, not more of it at once.
    const batch = await listPages(repoId, {
      ...opts,
      limit: PAGE_SIZE,
      offset,
      fields,
    } as ListPagesOpts & { fields: "summary" });
    all.push(...batch);
    if (batch.length < PAGE_SIZE) break;
    offset += PAGE_SIZE;
  }

  return all;
}

/**
 * Get page by its ID using the query-param endpoint (avoids path conflicts).
 *
 * Pass `repoId` whenever the caller knows it: a workspace server keeps a
 * separate store per repo and routes on that value, so without it the lookup
 * only ever reaches the default store.
 */
export async function getPageById(
  pageId: string,
  repoId?: string,
): Promise<PageResponse> {
  return apiGet<PageResponse>("/api/pages/lookup", {
    page_id: pageId,
    ...(repoId ? { repo_id: repoId } : {}),
  });
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
 * Force-regenerate a single page by ID. Launches the job immediately and
 * returns its id + stream token (D1/D5).
 *
 * `cascade` controls the pages that summarize this one — `none` (default)
 * touches only this page, `dependents` also refreshes its module/layer/overview
 * containers, `full` refreshes repo-wide. Pass `style` for a per-page wiki-style
 * override; omit it to use the repo's style.
 */
export async function regeneratePage(
  pageId: string,
  opts?: { cascade?: GenerateCascade; style?: string },
): Promise<JobLaunchResponse> {
  return apiPost<JobLaunchResponse>(
    "/api/pages/lookup/regenerate",
    undefined,
    undefined,
    {
      page_id: pageId,
      ...(opts?.cascade ? { cascade: opts.cascade } : {}),
      ...(opts?.style ? { style: opts.style } : {}),
    },
  );
}
