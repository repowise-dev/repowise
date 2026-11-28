/**
 * The docs panel's data, in the web explorer's shape: page summaries for the
 * tree and the palette, and the one page the reader shows, fetched by id.
 * Webviews never fetch: every call is an RPC the host serves.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { DocPage, DocPageSummary } from "@repowise-dev/types/docs";
import {
  buildPresentModel,
  canPresent,
  loadPresentSource,
  type PresentModel,
} from "@repowise-dev/ui/present";
import type { ViewParams } from "../../../../src/shared/webviewMessages";
import type { WebviewHost } from "../../runtime/rpc";
import { getVsCodeApi } from "../../runtime/vscodeApi";

function message(err: unknown): string {
  return err instanceof Error ? err.message : String(err);
}

/** Every page without its body. A refresh refetches it in place. */
function usePageList(host: WebviewHost, refreshToken: number) {
  const [pages, setPages] = useState<DocPageSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let cancelled = false;
    setError(null);
    host.api
      .pageTree()
      .then((list) => {
        if (!cancelled) setPages(list as unknown as DocPageSummary[]);
      })
      .catch((err: unknown) => {
        if (!cancelled) setError(message(err));
      });
    return () => {
      cancelled = true;
    };
  }, [host, refreshToken]);
  return { pages, error };
}

/**
 * The reader's page survives a tab switch: the panel's script re-runs when it
 * is shown again, and VS Code keeps this per-panel state across that. Wrapped
 * because there is no webview API outside the editor (tests, a harness).
 */
function savedPageId(): string | null {
  try {
    const state = getVsCodeApi().getState() as { docsPageId?: unknown } | undefined;
    return typeof state?.docsPageId === "string" ? state.docsPageId : null;
  } catch {
    return null;
  }
}

function savePageId(pageId: string): void {
  try {
    const api = getVsCodeApi();
    const state = api.getState();
    api.setState({ ...(state && typeof state === "object" ? state : {}), docsPageId: pageId });
  } catch {
    // No webview API: nothing to keep the page in.
  }
}

/**
 * The page to open on, resolved once per mount so a refresh never moves the
 * reader. The last page read wins on a plain open; an asked-for page id opens straight away, beside the list; a file
 * path resolves through the list, then the file's own page link.
 */
function useInitialPage(
  host: WebviewHost,
  params: ViewParams["docs"],
  pages: DocPageSummary[] | null,
  setSelectedPageId: (update: (current: string | null) => string | null) => void,
) {
  const resolved = useRef(false);
  useEffect(() => {
    if (resolved.current) return;
    // A plain open (no page or file asked for) returns to the page last read.
    const saved = params.pageId || params.filePath ? null : savedPageId();
    if (saved) {
      resolved.current = true;
      setSelectedPageId(() => saved);
      return;
    }
    if (params.pageId) {
      resolved.current = true;
      setSelectedPageId(() => params.pageId ?? null);
      return;
    }
    if (!pages) return;
    resolved.current = true;
    // A page picked while this resolves wins over it.
    const choose = (id: string | null | undefined) => setSelectedPageId((cur) => cur ?? id ?? null);
    const overview = (pages.find((p) => p.page_type === "repo_overview") ?? pages[0])?.id;
    const filePath = params.filePath;
    if (!filePath) return choose(overview);
    const byPath = pages.find((p) => p.page_type === "file_page" && p.target_path === filePath);
    if (byPath) return choose(byPath.id);
    host.api
      .fileDetail(filePath)
      .then((detail) => choose(detail.wiki_page?.id ?? overview))
      .catch(() => choose(overview));
  }, [host, pages, params.pageId, params.filePath, setSelectedPageId]);
}

/** The reader's page, fetched by id; refetched when the index moves. */
function useSelectedPage(host: WebviewHost, selectedPageId: string | null, refreshToken: number) {
  const [selected, setSelected] = useState<{ id: string; page: DocPage | null } | null>(null);
  useEffect(() => {
    if (!selectedPageId) return;
    let cancelled = false;
    host.api
      .pageById(selectedPageId)
      .then((page) => {
        if (!cancelled) setSelected({ id: selectedPageId, page: page as unknown as DocPage });
      })
      .catch(() => {
        if (!cancelled) setSelected({ id: selectedPageId, page: null });
      });
    return () => {
      cancelled = true;
    };
  }, [host, selectedPageId, refreshToken]);
  return {
    page: selected && selected.id === selectedPageId ? selected.page : null,
    loading: selectedPageId !== null && selected?.id !== selectedPageId,
  };
}

export function useDocsData(host: WebviewHost, params: ViewParams["docs"], refreshToken: number) {
  const list = usePageList(host, refreshToken);
  const [selectedPageId, setSelectedPageId] = useState<string | null>(null);
  useInitialPage(host, params, list.pages, setSelectedPageId);
  const selected = useSelectedPage(host, selectedPageId, refreshToken);
  useEffect(() => {
    if (selectedPageId) savePageId(selectedPageId);
  }, [selectedPageId]);
  return {
    pages: list.pages,
    listError: list.error,
    selectedPageId,
    setSelectedPageId,
    selectedPage: selected.page,
    pageLoading: selected.loading,
  };
}

/**
 * Present: a short deck from the same pages, whose few bodies are fetched
 * when it opens. A failed fetch is reported, not swallowed.
 */
export function usePresent(host: WebviewHost, pages: DocPageSummary[] | null) {
  const [model, setModel] = useState<PresentModel | null>(null);
  const [error, setError] = useState<string | null>(null);
  const presentable = useMemo(() => (pages ? canPresent(pages) : false), [pages]);
  const open = useCallback(async () => {
    if (!pages) return;
    setError(null);
    try {
      const source = await loadPresentSource(
        pages,
        (id) => host.api.pageById(id) as unknown as Promise<DocPage>,
      );
      if (source) setModel(buildPresentModel(source));
      else setError("There is not enough documentation to present yet.");
    } catch (err) {
      setError(message(err));
    }
  }, [host, pages]);
  const close = useCallback(() => setModel(null), []);
  return { presentable, model, error, open, close };
}

/**
 * The palette's body search. Titles and paths match in the palette itself;
 * bodies match on the server, mapped back onto the loaded rows.
 */
export function useDocsSearch(host: WebviewHost, pages: DocPageSummary[] | null) {
  return useCallback(
    async (q: string) => {
      const results = await host.api.searchPages(q);
      const byId = new Map((pages ?? []).map((p) => [p.id, p]));
      const seen = new Set<string>();
      const hits: { page: DocPageSummary; snippet?: string }[] = [];
      for (const r of results) {
        const page = byId.get(r.page_id);
        if (!page || seen.has(page.id)) continue;
        seen.add(page.id);
        hits.push({ page, ...(r.snippet ? { snippet: r.snippet } : {}) });
      }
      return hits;
    },
    [host, pages],
  );
}
