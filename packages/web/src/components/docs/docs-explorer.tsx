"use client";

import { useState, useCallback, useEffect, useMemo } from "react";
import useSWR from "swr";
import { useSearchParams, useRouter } from "next/navigation";
import { BookOpen, PanelLeftClose, PanelLeft, Search } from "lucide-react";
import { cn } from "@/lib/utils/cn";
import { usePages } from "@/lib/hooks/use-pages";
import { DocsTree } from "@repowise-dev/ui/docs/docs-tree";
import { DocsCommandPalette } from "@repowise-dev/ui/docs/command-palette";
import {
  PresentButton,
  PresentOverlay,
  buildPresentModel,
  canPresent,
  loadPresentPages,
  type PresentMode,
} from "@repowise-dev/ui/present";
import {
  DEFAULT_PERSONA,
  type ReaderPersona,
  isReaderPersona,
  personaFilteringApplies,
} from "@repowise-dev/ui/docs/reader-persona";
import { DocsHeader } from "./docs-header";
import { DocsViewer } from "./docs-viewer";
import {
  DocsPageActions,
  ExportMenu,
  SidebarToggle,
} from "./docs-page-actions";
import { PageGenerateButton } from "./page-generate-button";
import { BulkGenerateButton } from "./bulk-generate-button";
import { isModelWrittenType, isStubPage } from "@repowise-dev/ui/lib/page-types";
import { search as searchPages } from "@/lib/api/search";
import { getPageById, listAllPages } from "@/lib/api/pages";
import { downloadTextFile } from "@/lib/utils/download";
import { Skeleton } from "@repowise-dev/ui/ui/skeleton";
import type { DocPage, DocPageSummary } from "@repowise-dev/types/docs";
import type { PageSummary } from "@/lib/api/types";

interface DocsExplorerProps {
  repoId: string;
}

export function DocsExplorer({ repoId }: DocsExplorerProps) {
  const { pages, isLoading, mutate } = usePages(repoId);
  // The list carries no bodies, so the reader's page is fetched on its own.
  // Only the id is state; the page itself is whatever that id resolves to,
  // which keeps it correct after a regeneration without a second copy to sync.
  const [selectedPageId, setSelectedPageId] = useState<string | null>(null);
  const {
    data: selectedPage = null,
    isLoading: pageLoading,
    mutate: mutateSelectedPage,
  } = useSWR(
    selectedPageId ? `page:${selectedPageId}` : null,
    () => getPageById(selectedPageId!, repoId),
    // No retry: the one expected failure is a ?page= id that no longer exists,
    // and retrying a 404 just delays the empty state.
    { revalidateOnFocus: false, shouldRetryOnError: false },
  );
  const [treePanelOpen, setTreePanelOpen] = useState(() => {
    if (typeof window === "undefined") return true;
    return window.matchMedia("(min-width: 768px)").matches;
  });
  // The rail defaults to whether the layout can afford it, not to `true`.
  // Mirrors the 2xl breakpoint the reader uses to place its sections; a manual
  // toggle wins from then on, same contract as the app sidebar's route-based
  // collapse.
  const [sidebarOpen, setSidebarOpen] = useState(() => {
    if (typeof window === "undefined") return true;
    return window.matchMedia("(min-width: 1536px)").matches;
  });
  const [searchOpen, setSearchOpen] = useState(false);
  const [isExporting, setIsExporting] = useState(false);
  const searchParams = useSearchParams();
  const router = useRouter();

  // Reader persona — a client-side section filter, persisted in the URL
  // (?reader=) so a chosen depth is shareable and survives navigation. Owned
  // here (not in the viewer) because the control renders in the DocsHeader.
  const readerParam = searchParams.get("reader");
  const persona: ReaderPersona = isReaderPersona(readerParam) ? readerParam : DEFAULT_PERSONA;
  const setPersona = useCallback(
    (next: ReaderPersona) => {
      const params = new URLSearchParams(searchParams.toString());
      if (next === DEFAULT_PERSONA) params.delete("reader");
      else params.set("reader", next);
      const qs = params.toString();
      router.replace(qs ? `?${qs}` : "?", { scroll: false });
    },
    [router, searchParams],
  );
  // The reader control only renders when filtering would change this page —
  // on curated pages (guided tour, overviews) it's a no-op, so it hides.
  const personaHasEffect = useMemo(
    () => (selectedPage ? personaFilteringApplies(selectedPage.content) : false),
    [selectedPage],
  );

  // The docs header offers one bulk "write the subsystem pages" action, shown
  // only while concept pages are still stubs. A structural page is never a stub.
  const hasStubs = useMemo(
    () => pages.some((p) => isStubPage(p)),
    [pages],
  );

  // Keep the selected page in sync with the ?page= URL param. This fires on
  // mount and whenever the param changes — including when an in-content wiki
  // link or breadcrumb navigates via <Link href="?page=...">.
  const pageParam = searchParams.get("page");
  useEffect(() => {
    if (pageParam) {
      // Set straight from the URL without waiting for the list: a page id is
      // enough to fetch it, so the reading column and the tree load side by
      // side rather than one behind the other. An id that turns out not to
      // exist resolves to the reader's empty state, same as before.
      setSelectedPageId(pageParam);
      return;
    }
    // No ?page= in the URL — open the repo overview by default (falling back
    // to the first page) so the viewer never lands on an empty state.
    if (selectedPageId || pages.length === 0) return;
    const overview = pages.find((p) => p.page_type === "repo_overview");
    setSelectedPageId((overview ?? pages[0])?.id ?? null);
  }, [pages, pageParam, selectedPageId]);

  // After a page is (re)generated, pull the fresh list (freshness and stub
  // state live there) and refetch the page on screen so its content and
  // provenance flip in place.
  const handleGenerated = useCallback(async () => {
    await Promise.all([mutate(), mutateSelectedPage()]);
  }, [mutate, mutateSelectedPage]);

  const handleSelectPage = useCallback((page: DocPageSummary) => {
    setSelectedPageId(page.id);
    // Update URL without full navigation
    const params = new URLSearchParams(searchParams.toString());
    params.set("page", page.id);
    router.replace(`?${params.toString()}`, { scroll: false });
  }, [searchParams, router]);

  // Present mode — an on-the-fly slide deck + guided walkthrough over the same
  // loaded pages. Open state lives in ?present=deck|walkthrough so a specific
  // mode is shareable. The model is derived, never generated or fetched.
  const presentable = useMemo(() => canPresent(pages), [pages]);
  const presentParam = searchParams.get("present");
  const presentMode: PresentMode | null =
    presentParam === "walkthrough" ? "walkthrough" : presentParam === "deck" ? "deck" : null;
  // A deck draws on a couple of dozen pages out of thousands, so their bodies
  // are fetched when Present is opened rather than carried by the page list.
  const { data: presentModel = null } = useSWR(
    presentable && presentMode ? `present:${repoId}` : null,
    async () => {
      const source = await loadPresentPages(pages, (id) =>
        getPageById(id, repoId) as Promise<DocPage>,
      );
      return source.length > 0 ? buildPresentModel(source) : null;
    },
    { revalidateOnFocus: false },
  );
  const setPresent = useCallback(
    (mode: PresentMode | null, pageId?: string) => {
      const params = new URLSearchParams(searchParams.toString());
      if (mode) params.set("present", mode);
      else params.delete("present");
      if (pageId) params.set("page", pageId);
      const qs = params.toString();
      router.replace(qs ? `?${qs}` : "?", { scroll: false });
    },
    [searchParams, router],
  );
  const openInReaderFromPresent = useCallback(
    (pageId: string) => {
      setSelectedPageId(pageId);
      setPresent(null, pageId);
    },
    [setPresent],
  );

  // Server-backed search for the ⌘K palette. The palette matches titles and
  // paths itself; bodies are matched here, because the loaded list carries
  // none. Both search modes run: full-text is the literal body match the
  // palette used to do client-side, semantic finds pages that mean the same
  // thing without sharing a word. Results map back to the loaded rows so
  // selection behaves identically to a local hit; unknown ids are dropped.
  const searchFn = useCallback(
    async (q: string) => {
      const [fulltext, semantic] = await Promise.all([
        searchPages(q, { repo_id: repoId, limit: 20, search_type: "fulltext" }),
        searchPages(q, { repo_id: repoId, limit: 20, search_type: "semantic" }),
      ]);
      const byId = new Map(pages.map((p) => [p.id, p]));
      const seen = new Set<string>();
      const hits: { page: PageSummary; snippet?: string }[] = [];
      for (const r of [...fulltext, ...semantic]) {
        const page = byId.get(r.page_id);
        if (!page || seen.has(page.id)) continue;
        seen.add(page.id);
        hits.push({ page, ...(r.snippet ? { snippet: r.snippet } : {}) });
      }
      return hits;
    },
    [repoId, pages],
  );

  // Every page's markdown in one file. The bodies aren't loaded — that is the
  // point of the summary listing — so this is the one place that still asks
  // for the whole wiki, at the moment someone asks for the whole wiki.
  const handleExportAll = useCallback(async () => {
    setIsExporting(true);
    try {
      const full = await listAllPages(repoId);
      const sorted = [...full].sort((a, b) =>
        a.target_path.localeCompare(b.target_path),
      );
      const content = sorted
        .map((p) => `# ${p.title}\n\n> ${p.target_path}\n\n${p.content}`)
        .join("\n\n---\n\n");
      downloadTextFile(content, "documentation-export.md");
    } finally {
      setIsExporting(false);
    }
  }, [repoId]);

  // The tree panel, rendered at every state so it keeps the full height of the
  // window rather than starting below a chrome bar. Its skeleton lives here
  // too, so the layout does not reflow once the pages land.
  const treePanel = (
    // No right border. Tree, reading column and rail sit on one plane and are
    // separated by space; a rule on each side of the reading column made it
    // read as a trench between two panels.
    // Below md the tree is a drawer over the reader, not a column beside it.
    // As a column it would claim the full width and starve the header column
    // it now sits next to, squashing the view switch into a few pixels.
    <div
      className={cn(
        "bg-[var(--color-bg-surface)] transition-all duration-200 shrink-0 overflow-y-auto",
        treePanelOpen
          ? "absolute inset-y-0 left-0 z-30 w-full md:static md:w-[288px]"
          : "w-0 overflow-hidden",
      )}
    >
      {isLoading ? (
        <div className="space-y-2 p-3">
          <Skeleton className="h-8 w-full rounded-md" />
          <Skeleton className="h-4 w-3/4 rounded" />
          <Skeleton className="h-4 w-1/2 rounded" />
          <Skeleton className="h-4 w-5/6 rounded" />
          <Skeleton className="h-4 w-2/3 rounded" />
          <Skeleton className="h-4 w-3/4 rounded" />
          <Skeleton className="h-4 w-1/2 rounded" />
        </div>
      ) : (
        <DocsTree
          pages={pages}
          // The id, not the fetched page: the row should highlight on click,
          // not once its body has come back.
          selectedPageId={selectedPageId}
          onSelectPage={(p) => {
            handleSelectPage(p);
            if (typeof window !== "undefined" && !window.matchMedia("(min-width: 768px)").matches) {
              setTreePanelOpen(false);
            }
          }}
        />
      )}
    </div>
  );

  // The reader no longer waits on the page list — it has an id from the URL
  // and fetches its own page — so the only thing that blocks on the list is
  // knowing whether there is any documentation at all.
  let body: React.ReactNode;
  if (!isLoading && pages.length === 0) {
    body = (
      <div className="flex flex-col items-center justify-center h-full gap-4 text-center px-8">
        <div className="rounded-full bg-[var(--color-bg-elevated)] border border-[var(--color-border-default)] p-4">
          <BookOpen className="h-8 w-8 text-[var(--color-text-tertiary)]" />
        </div>
        <div className="space-y-1">
          <h3 className="text-sm font-semibold text-[var(--color-text-primary)]">
            No documentation yet
          </h3>
          <p className="text-xs text-[var(--color-text-secondary)] max-w-sm">
            Run a generation job to create AI-powered documentation for this codebase.
          </p>
        </div>
      </div>
    );
  } else {
    body = (
      <div className="h-full min-w-0">
        <DocsViewer
          page={selectedPage}
          pages={pages}
          repoId={repoId}
          // Reading-column skeleton while its own page is in flight, and while
          // the list is still deciding which page to open on.
          isLoading={pageLoading || (!selectedPageId && isLoading)}
          onSelectPage={handleSelectPage}
          persona={persona}
          sidebarOpen={sidebarOpen}
          onGenerated={handleGenerated}
        />
      </div>
    );
  }

  // Tree beside the chrome, not under it. The header used to span the full
  // width, which pushed the tree down by its height on every screen and cost
  // that much of the list for a bar that says "Documentation" on a page whose
  // route is already /docs. The tree now starts at the top and the bar sits to
  // the right of it, over the reading column it actually acts on.
  return (
    <div className="relative flex h-full">
      {treePanel}

      <button
        onClick={() => setTreePanelOpen((o) => !o)}
        aria-label={treePanelOpen ? "Hide pages tree" : "Show pages tree"}
        aria-expanded={treePanelOpen}
        className={cn(
          "absolute top-3.5 z-20 rounded-md p-1 text-[var(--color-text-tertiary)] hover:text-[var(--color-text-primary)] hover:bg-[var(--color-bg-elevated)] transition-colors",
          treePanelOpen ? "hidden md:block left-[292px]" : "left-1",
        )}
      >
        {treePanelOpen ? (
          <PanelLeftClose className="h-3.5 w-3.5" />
        ) : (
          <PanelLeft className="h-3.5 w-3.5" />
        )}
      </button>

      <div className="flex flex-1 min-w-0 flex-col">
        <DocsHeader>
          {selectedPage && (
            <DocsPageActions
              page={selectedPage}
              persona={persona}
              setPersona={setPersona}
              personaHasEffect={personaHasEffect}
            />
          )}
          {selectedPage && isModelWrittenType(selectedPage.page_type) && (
            <PageGenerateButton
              page={selectedPage}
              repoId={repoId}
              onGenerated={handleGenerated}
            />
          )}
          {hasStubs && <BulkGenerateButton repoId={repoId} onGenerated={handleGenerated} />}
          <button
            onClick={() => setSearchOpen(true)}
            className="flex items-center gap-2 rounded-md border border-[var(--color-border-default)] bg-[var(--color-bg-elevated)] px-2.5 py-1.5 text-xs text-[var(--color-text-tertiary)] transition-colors hover:text-[var(--color-text-secondary)]"
          >
            <Search className="h-3.5 w-3.5" />
            <span className="hidden sm:inline">Search</span>
            <kbd className="hidden rounded border border-[var(--color-border-default)] px-1 py-0.5 text-[10px] sm:inline">
              ⌘K
            </kbd>
          </button>
          <ExportMenu
            isExporting={isExporting}
            onExportAll={handleExportAll}
            zipHref={`/api/repos/${repoId}/export`}
            page={selectedPage}
            repoId={repoId}
          />
          {presentable && <PresentButton onClick={() => setPresent("deck")} />}
          {selectedPage && (
            <SidebarToggle
              open={sidebarOpen}
              onToggle={() => setSidebarOpen((o) => !o)}
            />
          )}
        </DocsHeader>

        <div className="flex-1 min-h-0">{body}</div>
      </div>

      {/* Present mode overlay — full-screen, escapes the dashboard chrome */}
      {presentMode && presentModel && (
        <PresentOverlay
          model={presentModel}
          initialMode={presentMode}
          onClose={() => setPresent(null)}
          onModeChange={(m) => setPresent(m)}
          onOpenPage={openInReaderFromPresent}
        />
      )}

      {/* ⌘K full-text command palette over loaded pages */}
      <DocsCommandPalette
        pages={pages}
        open={searchOpen}
        onOpenChange={setSearchOpen}
        onSelect={handleSelectPage}
        searchFn={searchFn}
      />
    </div>
  );
}
