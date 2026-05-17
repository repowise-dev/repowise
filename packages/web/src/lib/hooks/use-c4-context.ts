"use client";

/**
 * Side-data hooks for the C4 inspector.
 *
 * - useC4DocsPathSet: one fetch per repo → Set of paths that already have a
 *   generated wiki page. Used to render the "has docs" badge on container /
 *   component nodes without N+1 round-trips.
 *
 * - useC4SelectionContext: per-selection fetch → module health + page body
 *   for the active container/component path. Skipped for system / person /
 *   external selections.
 */

import { useMemo } from "react";
import useSWR from "swr";
import { listAllPages, getPageById } from "@/lib/api/pages";
import { getModuleHealth } from "@/lib/api/modules";
import type { ModuleHealthDetail, PageResponse } from "@/lib/api/types";

const SWR_OPTS = { revalidateOnFocus: false, revalidateOnReconnect: false };

const DOC_PAGE_TYPES = new Set(["module_page", "file_page"]);

interface DocsIndex {
  pathSet: ReadonlySet<string>;
  pageIdByPath: ReadonlyMap<string, string>;
}

const EMPTY_DOCS_INDEX: DocsIndex = {
  pathSet: new Set(),
  pageIdByPath: new Map(),
};

export function useC4DocsPathSet(repoId: string | null): DocsIndex {
  const { data } = useSWR<PageResponse[]>(
    repoId ? `c4-docs-index:${repoId}` : null,
    () => listAllPages(repoId!),
    SWR_OPTS,
  );
  return useMemo(() => {
    if (!data) return EMPTY_DOCS_INDEX;
    const pathSet = new Set<string>();
    const pageIdByPath = new Map<string, string>();
    for (const p of data) {
      if (!DOC_PAGE_TYPES.has(p.page_type)) continue;
      if (!p.target_path) continue;
      pathSet.add(p.target_path);
      // Prefer module_page over file_page when both exist for the same path.
      const existingId = pageIdByPath.get(p.target_path);
      if (!existingId || p.page_type === "module_page") {
        pageIdByPath.set(p.target_path, p.id);
      }
    }
    return { pathSet, pageIdByPath };
  }, [data]);
}

export interface C4SelectionContext {
  health: ModuleHealthDetail | null;
  page: PageResponse | null;
  isLoading: boolean;
}

export function useC4SelectionContext(
  repoId: string | null,
  path: string | null,
  pageId: string | null,
): C4SelectionContext {
  const { data: health, isLoading: healthLoading } = useSWR<ModuleHealthDetail>(
    repoId && path ? `c4-health:${repoId}:${path}` : null,
    () => getModuleHealth(repoId!, path!),
    SWR_OPTS,
  );

  const { data: page, isLoading: pageLoading } = useSWR<PageResponse>(
    pageId ? `c4-page:${pageId}` : null,
    () => getPageById(pageId!),
    SWR_OPTS,
  );

  return {
    health: (health as ModuleHealthDetail | undefined) ?? null,
    page: (page as PageResponse | undefined) ?? null,
    isLoading: healthLoading || pageLoading,
  };
}
