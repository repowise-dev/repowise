"use client";

import useSWR from "swr";
import { listPageTree } from "@/lib/api/pages";
import type { PageSummary } from "@/lib/api/types";

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
    () => listPageTree(repoId!, opts?.page_type),
    { revalidateOnFocus: false },
  );
  return { pages: data ?? [], error, isLoading, mutate };
}
