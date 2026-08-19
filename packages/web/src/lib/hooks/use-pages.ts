"use client";

import useSWR from "swr";
import { listAllPages } from "@/lib/api/pages";
import type { PageSummary } from "@/lib/api/types";

/**
 * Page types whose row in a *list* is drawn from something a summary doesn't
 * carry: an onboarding slot lives in metadata, and a cycle page's label is
 * parsed out of its body. There are a couple of dozen such pages against
 * thousands of files, so they are fetched in full and merged in.
 *
 * The overview is here for the same reason it is worth having early anyway:
 * it is the page the reader opens on, and Present reads its guided tour.
 */
const HYDRATED_TYPES = [
  "repo_overview",
  "architecture_diagram",
  "onboarding",
  "scc_page",
] as const;

async function loadPages(
  repoId: string,
  pageType?: string,
): Promise<PageSummary[]> {
  const hydrating = pageType
    ? HYDRATED_TYPES.filter((t) => t === pageType)
    : HYDRATED_TYPES;

  const [summaries, ...hydrated] = await Promise.all([
    listAllPages(repoId, {
      ...(pageType ? { page_type: pageType } : {}),
      fields: "summary",
    }),
    ...hydrating.map((page_type) => listAllPages(repoId, { page_type })),
  ]);

  if (hydrated.length === 0) return summaries;
  const full = new Map(hydrated.flat().map((p) => [p.id, p]));
  return summaries.map((p) => full.get(p.id) ?? p);
}

/**
 * Every page in a repo, without the bodies.
 *
 * A full listing of this repo's 5,485 pages measured 38.6 MB over twelve
 * sequential round trips, of which `content` and `metadata` are 95% — and the
 * tree renders neither. The reader fetches the one page it is showing;
 * Present and Export fetch what they need when they are used.
 */
export function usePages(repoId: string | null, opts?: { page_type?: string }) {
  const { data, error, isLoading, mutate } = useSWR<PageSummary[]>(
    repoId ? `pages:${repoId}:${opts?.page_type ?? "all"}` : null,
    () => loadPages(repoId!, opts?.page_type),
    { revalidateOnFocus: false },
  );
  return { pages: data ?? [], error, isLoading, mutate };
}
