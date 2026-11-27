/**
 * Docs panel: the web docs explorer composed from the same shared pieces (the
 * pages tree, the header bar, the search palette, the reader with its reader
 * level, and Present), over the same data shape: page summaries for the tree
 * and the palette, and one page fetched by id for the reader.
 *
 * The explorer's model writes, exports, chat and hosted nudges are left out:
 * the panel writes nothing with a model, and a webview cannot download. A
 * file reference opens the file in an editor; a wiki link switches the page.
 */

import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { BookOpen, PanelLeft, PanelLeftClose, Presentation, Search } from "lucide-react";
import { ApiError } from "@repowise-dev/ui/shared";
import { Skeleton } from "@repowise-dev/ui/ui/skeleton";
import { cn } from "@repowise-dev/ui/lib/cn";
import type { DocPage, DocPageSummary } from "@repowise-dev/types/docs";
import { DocsTree } from "@repowise-dev/ui/docs/docs-tree";
import { DocsCommandPalette } from "@repowise-dev/ui/docs/command-palette";
import { DocsHeader, type DocsHeaderTab } from "@repowise-dev/ui/docs/docs-header";
import { DocsMoreMenu, type DocsMenuGroup } from "@repowise-dev/ui/docs/docs-more-menu";
import { DocsReader, type ReaderLinkComponent } from "@repowise-dev/ui/docs/docs-reader";
import { ReaderLevelControl, SidebarToggle } from "@repowise-dev/ui/docs/docs-page-actions";
import {
  DEFAULT_PERSONA,
  READER_PERSONAS,
  personaFilteringApplies,
  type ReaderPersona,
} from "@repowise-dev/ui/docs/reader-persona";
import {
  PresentOverlay,
  buildPresentModel,
  canPresent,
  loadPresentSource,
  type PresentModel,
} from "@repowise-dev/ui/present";
import type { ViewProps } from "../../runtime/mount";
import type { WebviewHost } from "../../runtime/rpc";
import { fileHref, useFileLinks } from "../../runtime/fileLinks";

/** The web explorer's English strings (`docs.explorer.*`, `views.docsTabs.*`, `common.search`). */
const COPY = {
  emptyTitle: "No documentation yet",
  emptyBody:
    "No pages yet. Indexing this repo builds docs from the code. To have a model write the how and why, add an API key, sign in to Claude Code, Codex or OpenCode, or run a local model.",
  hideTree: "Hide pages tree",
  showTree: "Show pages tree",
  more: "More",
  present: "Present",
  readerLevel: "Reader level",
  explorer: "Explorer",
  search: "Search",
} as const;

/** The panel has one docs view, so the switch carries the one tab. */
const TABS: DocsHeaderTab[] = [{ label: COPY.explorer, href: "?view=explorer", isActive: true, icon: "explorer" }];

const PAGE_QUERY = "?page=";

export function App({ host, params, repo, refreshToken }: ViewProps<"docs">) {
  useFileLinks(host);
  const [pages, setPages] = useState<DocPageSummary[] | null>(null);
  const [listError, setListError] = useState<string | null>(null);
  const [selectedPageId, setSelectedPageId] = useState<string | null>(null);
  const [selected, setSelected] = useState<{ id: string; page: DocPage | null } | null>(null);
  const [persona, setPersona] = useState<ReaderPersona>(DEFAULT_PERSONA);
  const [searchOpen, setSearchOpen] = useState(false);
  const [presentModel, setPresentModel] = useState<PresentModel | null>(null);
  // Both panels default to what the width affords, as on the web: the tree
  // from md, the rail from the 2xl breakpoint the reader places it at.
  const [treePanelOpen, setTreePanelOpen] = useState(true);
  const [sidebarOpen, setSidebarOpen] = useState(true);
  useLayoutEffect(() => {
    setTreePanelOpen(window.matchMedia?.("(min-width: 768px)").matches ?? true);
    setSidebarOpen(window.matchMedia?.("(min-width: 1536px)").matches ?? true);
  }, []);

  // The list carries no bodies. A refresh refetches it in place.
  useEffect(() => {
    let cancelled = false;
    setListError(null);
    host.api
      .pageSummaries()
      .then((list) => {
        if (!cancelled) setPages(list as unknown as DocPageSummary[]);
      })
      .catch((err: unknown) => {
        if (!cancelled) setListError(err instanceof Error ? err.message : String(err));
      });
    return () => {
      cancelled = true;
    };
  }, [host, refreshToken]);

  // The page to open on, resolved once per mount so a refresh never moves the
  // reader. An asked-for page id opens straight away, beside the list; a file
  // path resolves through the list, then the file's own page link.
  const resolved = useRef(false);
  useEffect(() => {
    if (resolved.current) return;
    if (params.pageId) {
      resolved.current = true;
      setSelectedPageId(params.pageId);
      return;
    }
    if (!pages) return;
    resolved.current = true;
    const overview = pages.find((p) => p.page_type === "repo_overview") ?? pages[0];
    const filePath = params.filePath;
    if (!filePath) {
      setSelectedPageId(overview?.id ?? null);
      return;
    }
    const byPath = pages.find((p) => p.page_type === "file_page" && p.target_path === filePath);
    if (byPath) {
      setSelectedPageId(byPath.id);
      return;
    }
    host.api
      .fileDetail(filePath)
      .then((detail) => setSelectedPageId(detail.wiki_page?.id ?? overview?.id ?? null))
      .catch(() => setSelectedPageId(overview?.id ?? null));
  }, [host, pages, params.pageId, params.filePath]);

  // The reader's page, fetched by id; refetched when the index moves.
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

  const selectedPage = selected && selected.id === selectedPageId ? selected.page : null;
  const pageLoading = selectedPageId !== null && selected?.id !== selectedPageId;

  const selectPage = useCallback((page: DocPageSummary) => setSelectedPageId(page.id), []);
  const selectPageFromTree = useCallback((page: DocPageSummary) => {
    setSelectedPageId(page.id);
    if (!window.matchMedia?.("(min-width: 768px)").matches) setTreePanelOpen(false);
  }, []);

  const LinkComponent = useMemo(() => makeLink(host, setSelectedPageId), [host]);
  const buildPageHref = useCallback((pageId: string) => `${PAGE_QUERY}${encodeURIComponent(pageId)}`, []);
  const buildFileHref = useCallback((filePath: string) => fileHref(filePath), []);

  const personaHasEffect = useMemo(
    () => (selectedPage ? personaFilteringApplies(selectedPage.content) : false),
    [selectedPage],
  );

  // Present draws on a handful of pages, fetched when it opens.
  const presentable = useMemo(() => (pages ? canPresent(pages) : false), [pages]);
  const openPresent = useCallback(async () => {
    if (!pages) return;
    const source = await loadPresentSource(
      pages,
      (id) => host.api.pageById(id) as unknown as Promise<DocPage>,
    );
    if (source) setPresentModel(buildPresentModel(source));
  }, [host, pages]);

  // Titles and paths match in the palette itself; bodies match on the server.
  const searchFn = useCallback(
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

  // Markdown renders external links as bare anchors the reader does not own;
  // they open in the browser rather than navigating the webview.
  const openExternalLinks = useCallback(
    (event: React.MouseEvent) => {
      if (event.metaKey || event.ctrlKey || event.shiftKey || event.button !== 0) return;
      const href = (event.target as HTMLElement).closest("a")?.getAttribute("href");
      if (href && /^https?:\/\//i.test(href)) {
        event.preventDefault();
        host.openExternal(href);
      }
    },
    [host],
  );

  const menuGroups: DocsMenuGroup[] = [
    {
      key: "present",
      items: presentable
        ? [{ key: "present", label: COPY.present, icon: Presentation, onSelect: () => void openPresent() }]
        : [],
    },
    {
      key: "reader",
      label: COPY.readerLevel,
      phoneOnly: true,
      items: personaHasEffect
        ? READER_PERSONAS.map((p) => ({
            key: `reader-${p.value}`,
            label: p.label,
            checked: persona === p.value,
            onSelect: () => setPersona(p.value),
          }))
        : [],
    },
  ];

  if (listError && !pages) {
    return (
      <div className="flex h-full items-center justify-center px-8">
        <ApiError title="Could not load docs" message={listError} />
      </div>
    );
  }

  const listLoading = pages === null;
  const treePanel = (
    <div
      className={cn(
        "shrink-0 overflow-y-auto bg-[var(--color-bg-surface)] transition-all duration-200",
        treePanelOpen ? "absolute inset-y-0 left-0 z-30 w-full md:static md:w-[288px]" : "w-0 overflow-hidden",
      )}
    >
      {listLoading ? (
        <div className="space-y-2 p-3" aria-label="Loading pages">
          <Skeleton className="h-8 w-full rounded-md" />
          {["w-3/4", "w-1/2", "w-5/6", "w-2/3", "w-3/4", "w-1/2"].map((w, i) => (
            <Skeleton key={i} className={`h-4 ${w} rounded`} />
          ))}
        </div>
      ) : (
        <DocsTree pages={pages} selectedPageId={selectedPageId} onSelectPage={selectPageFromTree} />
      )}
    </div>
  );

  const body =
    !listLoading && pages.length === 0 ? (
      <div className="flex h-full flex-col items-center justify-center gap-4 px-8 text-center">
        <div className="rounded-full border border-[var(--color-border-default)] bg-[var(--color-bg-elevated)] p-4">
          <BookOpen className="h-8 w-8 text-[var(--color-text-tertiary)]" />
        </div>
        <div className="space-y-1">
          <h3 className="text-sm font-semibold text-[var(--color-text-primary)]">{COPY.emptyTitle}</h3>
          <p className="max-w-sm text-xs text-[var(--color-text-secondary)]">{COPY.emptyBody}</p>
        </div>
      </div>
    ) : (
      <div className="h-full min-w-0">
        <DocsReader
          page={selectedPage}
          pages={pages ?? []}
          repoId={repo.id}
          isLoading={pageLoading || (!selectedPageId && listLoading)}
          missingPageId={selectedPageId && !pageLoading && !selectedPage ? selectedPageId : undefined}
          onSelectPage={selectPage}
          onNavigatePageId={setSelectedPageId}
          persona={persona}
          sidebarOpen={sidebarOpen}
          buildPageHref={buildPageHref}
          buildFileHref={buildFileHref}
          LinkComponent={LinkComponent}
        />
      </div>
    );

  return (
    <div className="relative flex h-full bg-[var(--color-bg-root)]">
      {treePanel}

      <button
        onClick={() => setTreePanelOpen((o) => !o)}
        aria-label={treePanelOpen ? COPY.hideTree : COPY.showTree}
        aria-expanded={treePanelOpen}
        className={cn(
          "absolute top-3.5 z-40 rounded-md p-1 text-[var(--color-text-tertiary)] transition-colors hover:bg-[var(--color-bg-elevated)] hover:text-[var(--color-text-primary)]",
          treePanelOpen ? "hidden md:block left-[292px]" : "left-1",
        )}
      >
        {treePanelOpen ? <PanelLeftClose className="h-3.5 w-3.5" /> : <PanelLeft className="h-3.5 w-3.5" />}
      </button>

      <div className={cn("flex min-w-0 flex-1 flex-col", !treePanelOpen && "pl-6")}>
        <DocsHeader tabs={TABS} LinkComponent={LinkComponent}>
          {personaHasEffect && (
            <ReaderLevelControl persona={persona} setPersona={setPersona} className="hidden sm:inline-flex" />
          )}
          <button
            onClick={() => setSearchOpen(true)}
            aria-label={COPY.search}
            className="flex h-7 shrink-0 items-center gap-2 rounded-md border border-[var(--color-border-default)] bg-[var(--color-bg-elevated)] px-2 text-xs text-[var(--color-text-tertiary)] transition-colors hover:text-[var(--color-text-secondary)] sm:px-2.5"
          >
            <Search className="h-3.5 w-3.5" />
            <span className="hidden sm:inline">{COPY.search}</span>
            <kbd className="hidden rounded border border-[var(--color-border-default)] px-1 py-0.5 text-[10px] sm:inline">
              ⌘K
            </kbd>
          </button>
          {selectedPage && <SidebarToggle open={sidebarOpen} onToggle={() => setSidebarOpen((o) => !o)} />}
          <DocsMoreMenu label={COPY.more} groups={menuGroups} />
        </DocsHeader>

        <div className="min-h-0 flex-1" onClickCapture={openExternalLinks}>
          {body}
        </div>
      </div>

      {presentModel && (
        <PresentOverlay
          model={presentModel}
          onClose={() => setPresentModel(null)}
          onOpenPage={(pageId) => {
            setSelectedPageId(pageId);
            setPresentModel(null);
          }}
        />
      )}

      {pages && (
        <DocsCommandPalette
          pages={pages}
          open={searchOpen}
          onOpenChange={setSearchOpen}
          onSelect={selectPage}
          searchFn={searchFn}
        />
      )}
    </div>
  );
}

/**
 * Anchor for the reader and header: a `?page=` href switches the page in
 * place, an http(s) href opens in the browser, and file hrefs are left to the
 * document-level file-link listener.
 */
function makeLink(host: WebviewHost, openPage: (pageId: string) => void): ReaderLinkComponent {
  return function PanelLink({
    href,
    className,
    title,
    children,
    ...rest
  }: {
    href: string;
    className?: string;
    title?: string;
    children: React.ReactNode;
  }) {
    return (
      <a
        href={href}
        className={className}
        title={title}
        {...rest}
        onClick={(event) => {
          if (event.defaultPrevented) return;
          if (event.metaKey || event.ctrlKey || event.shiftKey || event.button !== 0) return;
          if (/^https?:\/\//i.test(href)) {
            event.preventDefault();
            host.openExternal(href);
          } else if (href.startsWith(PAGE_QUERY)) {
            event.preventDefault();
            openPage(decodeURIComponent(href.slice(PAGE_QUERY.length)));
          } else if (href.startsWith("?")) {
            // The header's own tab: there is no other view to switch to.
            event.preventDefault();
          }
        }}
      >
        {children}
      </a>
    );
  };
}
